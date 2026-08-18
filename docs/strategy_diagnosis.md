# ParayaBuck Strategy Diagnosis — V1 Root-Cause Attribution

Status: research evidence record (numbers were reproducible via
`scripts/strategy_diagnose.py`; that harness was retired 2026-08-18 with the
rejected implementations — see `docs/rejected_strategies.md` — and the
evidence below is frozen).

## Identity

```
HEAD:                3c0587864b7e861d13191a4d20d373980fc4ffa5
config digest:       config/settings.yaml at this HEAD (strategy.equity
                     equity_dynamic_trend_1d_v1: trend_window=150
                     momentum_window=60 volatility_window=20 top_n=6
                     max_single_weight=0.10 annual_volatility_target=0.10
                     correlation_floor=0.25 rebalance_frequency=weekly
                     maximum_allocation=0.50; risk: daily_loss_limit_pct=0.005
                     max_drawdown_pct=0.05 cash_buffer_pct=0.40
                     max_gross_exposure_pct=0.6 max_positions=6)
dataset:             data/historical/equity_1200_2019_2026.csv
                     sha256 20bb2fb58ac8e929cde2d24fad1af9b01c9f2bc98cd652fc53f1aa18728fabfd
                     1,194 symbols, 1,804 trading days,
                     2019-05-28 .. 2026-07-30 (1,364,743 rows)
universe policy:     build_trailing_point_in_time_universe (production scanner,
                     daily snapshots, Stage-A top-300 cap within the downloaded
                     pool, 60d trailing window, strictly prior bars)
strategy parameters: defaults above (no optimization)
reproducer:          python scripts/strategy_diagnose.py --mode all
                     python -m parayabuck.backtest --market equity --data <csv>
```

## 1. ENGINE SANITY

Read-only audit (scout A, all verified against source):

- **No P0 correctness defect found.** Fill model is "decision on completed bar T
  (close); fill at next tradable bar OPEN, sells before buys, participation
  cap = 10% of 20-bar average volume known at the decision, cash-checked".
  Pinned by tests (`test_product_backtest.py`, `test_portfolio_engine_accounting.py`).
- No lookahead: momentum score intentionally excludes the decision bar
  (`momentum_at(closes, 60, skip_bars=1)` measures [t-62, t-2]); universe
  snapshots admit only bars strictly before the snapshot; volume capacity is
  point-in-time; benchmark (SPY) from the same split-adjusted series.
- Correct warmup (>=151 bars), single cash ledger, fresh risk state per window,
  correctly-sided effective-dated regulatory fees.
- P1 (documented, not fixed): (a) dividends only credited when a
  corporate-actions CSV is supplied — recorded runs exclude ~1-2%/yr yield on
  held names (small downward bias, not a loss fabricator); (b) the all-or-
  nothing decision gate skips the whole weekly decision if any universe symbol
  lacks a bar at the decision timestamp (0 occurrences in the 2020-2024 window,
  so immaterial here); (c) reported "excess return" is arithmetic subtraction
  of geometric returns (reporting approximation only).

**Baseline reproduction (Phase 2) matches the recorded validation exactly:**

| Window | Recorded (docs/release_validation.md) | Reproduced |
|---|---|---|
| TRAIN 2020-2024 default | +4.86% / Sharpe 0.32 / maxDD 7.32% / 12 trades / avg exp 1.6% | +4.86% / 0.3226 / 7.32% / 12 / 1.56% |
| OOS 2025-2026 default | -5.30% / Sharpe -0.72 / maxDD 8.06% / 20 trades / avg exp 6.4% | -5.30% / -0.72 / 8.06% / 20 / 6.44% |

## 2. OPERATIONAL BREAKER ATTRIBUTION (Phase 3)

`NON-DEPLOYABLE ALPHA DIAGNOSTIC`: same strategy, scanner, sizing, costs,
allocation/position caps, volatility targeting; ONLY the two operational entry
latches (daily-loss limit, portfolio drawdown halt) are disabled via risk-limit
sentinels in the diagnostic script. Production safety unchanged; config
defaults unchanged.

| Metric | TRAIN production | TRAIN alpha-diagnostic | OOS production | OOS alpha-diagnostic |
|---|---|---|---|---|
| Return | +4.86% | **+70.04%** | -5.30% | **+13.47%** |
| CAGR | 0.95% | 11.20% | -3.41% | 8.38% |
| Sharpe | 0.32 | **1.03** | -0.72 | **0.83** |
| Sortino | 0.09 | 1.51 | -0.33 | 1.04 |
| Max drawdown | 7.32% | 13.33% | 8.06% | 12.93% |
| Trades | 12 | 409 | 20 | 152 |
| Turnover | 2.17 | 68.1 | 3.60 | 25.0 |
| Avg exposure | 1.6% | 43.5% | 6.4% | 44.8% |
| Max exposure | 50% | 50% | 50% | 50% |
| vs SPY | +80.4% / -75.5pp | +80.4% / -10.4pp | +26.9% / -32.2pp | +26.9% / -13.4pp |

**Incident log (where production rules tripped):**

- TRAIN: **255 breaker decisions** — 3 daily-loss, 254 drawdown-halt (overlap
  on 2 dates). The drawdown latch first trips 2020-02-24 (drawdown 5.02%);
  the book drains from 6 positions to 0 by 2020-03-16 via trend-break sells
  while the halt blocks the strategy's rotation into inverse hedges
  (DOG/SH/PSQ — the momentum leaders of the crash); equity then sits at
  104,864 vs peak 111,585 (6.02% drawdown) for the remaining 4.7 years.
- OOS: **79 breaker decisions** — 6 daily-loss, 76 drawdown-halt. Latch trips
  2025-03-04; book drains to 0 positions; equity 94,700 vs peak 102,308
  (7.44%) from 2025-03 to 2026-07.

**Recovery counterfactual:** without the latch, the alpha-diagnostic equity
series peaked at 109,651 on 2020-02-14, troughed at 102,752 on 2020-02-27
(-6.29%; the March 2020 low was 103,082 on 03-09 — the unhalted book rotated
into inverse hedges and never went below the February trough), and
**recovered to its pre-crash peak by 2020-07-01 (125 days after the
trough)** and continued to +70%. With the latch (cash book at 0% cash
yield), recovery is impossible by construction — equity must exceed 95% of
the all-time close-marked peak, which a flat cash book can never do.

**Verdict for this phase:** the operational drawdown latch is the dominant
explanation of the *flat/negative recorded results*. It converts a recoverable
6-8% drawdown into a permanent cash trap (97.2% of TRAIN days, 89.9% of OOS
days under the latch). The alpha-diagnostic portfolio is materially healthier
in every dimension except max drawdown (13.3% vs 7.3% — the price of staying
invested through the crash; still below SPY's 34.2%).

## 3. EXPOSURE DECOMPOSITION (Phase 4)

Per-day attribution of idle capital (reasons are non-exclusive; a day can be
in multiple buckets):

| Reason | TRAIN days | TRAIN avg expo when active | OOS days | OOS avg expo when active |
|---|---|---|---|---|
| DRAWDOWN_LATCH | **97.2%** (1223/1258) | 0.24% | **89.9%** (354/394) | 2.86% |
| VOLATILITY_SCALING | 71.1% | 1.30% | 73.6% | 5.97% |
| STRATEGY_CASH (design caps) | 0%* | — | 88.8% | 6.16% |
| DAILY_LOSS_LATCH | 1.2% | 27.1% | 5.1% | 34.1% |
| MAXIMUM_ALLOCATION | 1.7% | 47.4% | 2.5% | 46.4% |
| INTERMEDIATE_RISK | 0% | — | 1.3% | 36.6% |
| NO_QUALIFYING_SYMBOLS | 0% | — | 0% | — |
| MARKET_OR_UNIVERSE_NOT_READY | 0% | — | 0% | — |

\* TRAIN design-cap bucket reads 0 because the drawdown latch subsumes nearly
all invested days; the strategy's own target cash weight was 0.45-0.82 on
invested decisions (see below).

**Answer to "why was average exposure only 1.6%/6.4%?" (numerical):**
97.2% of TRAIN days (89.9% of OOS days) were under the drawdown latch with
average gross exposure 0.24% (2.86%). Even fully unlatched, the structural
design caps exposure: 6 names x 10% max single weight (sum <= 0.60) times the
vol-target multiplier `min(1, 0.10/sigma_p)` (correlation-floor portfolio vol;
empirically sigma_p 8-33% for the top-30 scanner names -> multiplier 0.31-1.0),
then the overlay's `maximum_allocation=0.50` cap. Worked example (6 names at
20-40% vol): sigma_p = 11.1%, multiplier 0.90, gross 54% -> capped at 50%.
Unlatched decisions in the V1 production run (7 non-halted weekly decisions
in 2020-01..2020-02, from reports/research/exposure/report.json) showed mean
risk_multiplier 0.935 and mean target cash 0.461; actual unlatched avg gross
exposure ~44-45%.

## 4. SIGNAL QUALITY (Phase 5)

V1 score = `momentum_at(60, skip 1)` (60-day formation, decision bar excluded)
gated by close > SMA150 and score > 0, on the point-in-time scanner-active
universe, evaluated weekly. Forward returns: next bar OPEN -> close[t+H]
(fill-consistent, leak-free).

**Rank IC (Spearman) and top-6 excess:**

| Horizon | TRAIN IC mean (hit) | TRAIN top-6 excess vs uni / vs SPY | SEEN 2025-2026 IC mean (hit) | SEEN top-6 excess vs uni / vs SPY |
|---|---|---|---|---|
| 5d | 0.017 (53%) | +0.33% / +0.25% | 0.042 (59%) | +0.30% / +0.00% |
| 21d | 0.013 (53%) | +1.31% / +0.69% | 0.002 (49%) | +1.45% / -0.05% |
| 63d | 0.080 (60%) | +3.67% / +3.32% | 0.031 (59%) | +4.32% / -0.30% |

**By year (21d IC):** 2020 +0.167 | 2021 +0.031 | 2022 **-0.093** | 2023 +0.010 |
2024 **-0.071** | 2025 -0.033 | 2026 +0.113.

**By regime:** SPY above SMA200: 21d IC +0.034 (TRAIN) / +0.070 (SEEN);
SPY below SMA200: 21d IC **-0.039** (TRAIN) / **-0.360** (SEEN, n=11).
High market vol: 63d IC +0.114; low vol: 21d IC -0.010.

**Reading:** the V1 score is a weak, horizon-mismatched signal. Its own
holding cadence is weekly (5-21d) where IC is ~0.01-0.04 and often negative
by year (2022, 2024, 2025) and in drawdown regimes (momentum-crash behavior:
21d IC -0.04..-0.36 when SPY is below trend). Only the 63d horizon shows a
consistent weak positive (IC 0.03-0.08), which the weekly strategy does not
capture. In the seen period, top-6 momentum names did NOT beat SPY at any
horizon (excess ~0 to -0.3pp/quarter). **Answer: higher V1 score does not
consistently predict better future returns at the strategy's holding horizon —
the alpha is weak.**

## 5. PNL ATTRIBUTION (Phase 6, alpha-diagnostic path)

- TRAIN: 698 round trips, win rate 64%, +$68.0k on $100k.
  **Concentration: NVDA (+$27.9k) + TSLA (+$20.9k) = 71% of total PnL.**
  Top-5 (NVDA, TSLA, AMD, GOOGL, AAPL) = +$68.5k vs total +$68.0k (all five
  together exceed the total — AMD/GOOGL/AAPL offset other losses).
  Best month (2023-05) = 16.2% of PnL; best year (2021) = 41.4%.
- OOS: 244 round trips, win rate 61%, +$12.4k. AMD = 49% of total.
  **Best year (2026) = 98.4% of total PnL; best month (2025-07) = 41.9%.**
- Median holding 14d (TRAIN) / 8d (OOS); exits 87-90% scheduled, remainder
  intermediate-risk trend exits.

**Reading:** profits are not broad. TRAIN's unlatched performance is largely
the 2020-2021 NVDA/TSLA momentum run; OOS is almost entirely 2026. This is the
fragile, crash-prone profile the momentum literature documents.

## 6. SCANNER EFFECT (Phase 7)

Same V1 score, same dates, 21d horizon, weekly:

| Universe | TRAIN IC mean | SEEN IC mean | weekly churn |
|---|---|---|---|
| Stage-A shortlist (top-300 by trailing med dollar vol) | **-0.022** | **-0.025** | 0.9% |
| Scanner-active (production, <=30) | **+0.012** | **-0.015** | 5.7% |

Median active size 30/30; the scanner removes ~11.3 of the top-decile
momentum names from Stage-A every week, and **that removal improves rank IC**
in both periods (Stage-A negative -> active less negative / positive). The
scanner's tradability filters (liquidity >= $20M med DV, price >= $5, ATR band,
completeness) and its score ranking do not destroy the signal opportunity set —
they improve it. **Verdict: scanner interaction is NOT the failure cause.**
(No scanner simplification is justified by the evidence.)

## 7. BENCHMARKS (Phase 8)

Same bar set, SPY series from the CSV:

| Benchmark | TRAIN return | Sharpe | maxDD | OOS return | Sharpe | maxDD |
|---|---|---|---|---|---|---|
| 100% SPY | +80.4% | 0.68 | 34.2% | +26.9% | 0.95 | 19.0% |
| 50% SPY / 50% cash | +40.2% | 0.68 | ~17% | +13.4% | 0.95 | ~10% |
| SPY vol-targeted (10% ann vol) | +42.9% | 0.72 | 15.3% | +11.5% | 0.67 | 12.5% |
| V1 alpha-diagnostic (43-45% expo) | +70.0% | 1.03 | 13.3% | +13.5% | 0.83 | 12.9% |

**Reading:** unlatched V1 beats the risk-matched (10% vol-targeted) SPY in
TRAIN (+70.0% vs +42.9%, Sharpe 1.03 vs 0.72, similar maxDD) and roughly ties
it in the seen period (+13.5% vs +11.5%, Sharpe 0.83 vs 0.67). It never beats
100% SPY in absolute terms (it runs at ~half the exposure). So the *system*
added risk-adjusted value in TRAIN when the latches were off, but the edge is
thin, concentrated, and did not repeat in 2025-2026.

## 8. COST ROBUSTNESS (alpha path)

| Window | 1x | 2x | 3x |
|---|---|---|---|
| TRAIN | +70.0% / Sh 1.03 | +66.6% / 0.99 | +63.2% / 0.95 |
| OOS | +13.5% / Sh 0.83 | +12.6% / 0.78 | +11.8% / 0.73 |

TRAIN total costs: $2,996 (slippage $1,930 + spread $965 + fees $101) = 3.0%
of equity, 4.1% of gross profit. **The edge survives 3x costs; execution cost
is NOT the failure cause** (turnover is high but per-unit costs are low).

## ROOT CAUSE VERDICT

```
A. SIGNAL FAILURE:            PRIMARY (weak, unstable, horizon-mismatched alpha)
C. OPERATIONAL BREAKER:       PROXIMATE cause of the recorded flat/negative results
B. SCANNER INTERACTION:       NOT a cause (scanner improves IC)
D. SIZING/RISK BUDGET:        NOT a cause (vol targeting behaves sensibly;
                              structural 36-54% exposure is the design)
E. COST:                      NOT a cause (3x costs barely dent Sharpe)
F. INSUFFICIENT DATA:         contributes (survivorship-limited 2019-2026 pool;
                              see data extension section)
```

Layered evidence, in order of decisiveness:

1. **The latch is the proximate cause of the recorded results.** Production
   exposure 1.6%/6.4% vs 43-45% unlatched; 97.2% / 89.9% of days under the
   drawdown latch; a 6.3% drawdown that the counterfactual recovers in 125
   days becomes a 4.75-year cash trap. `docs/release_validation.md`'s own
   interpretation is confirmed numerically.
2. **But removing the latch does not make V1 a good strategy.** Unlatched V1
   beats risk-matched SPY only in TRAIN, driven by NVDA+TSLA = 71% of PnL; in
   the seen period it merely ties vol-matched SPY and its top-6 signal does not
   beat SPY at any horizon. The 21d rank IC is ~0.01 and flips negative in
   2022/2024/2025 and in SPY-below-trend regimes.
3. **V1 is a horizon-mismatched construction**: 60-day formation momentum
   traded weekly (5-21d holding) whose only positive predictive power sits at
   the 63d horizon it never holds.

**Decision:** V1 is a weak alpha candidate (retire as the live candidate for
further optimization); the operational latch must also be addressed as a
separate, safety-preserving concern. Proceed to Conditional Path C: test ONE
literature-grounded long-horizon momentum candidate
(`equity_long_horizon_momentum_1d_v2`) on the existing scanner universe, with
the new 2016-2019 data as walk-forward evidence.

## NEXT ACTION

Executed: V2 long-horizon momentum candidate implemented and evaluated
(`equity_long_horizon_momentum_1d_v2`; see docs/strategy_validation_v2.md).
Component ablation removed the SPY regime gate and vol sizing (value-negative
on development evidence). Walk-forward on the newly extended 2016-2019 data
is weak (median fold Sharpe 0.39, +1.6% total); the stock-momentum family is
not validated and the mission stops at the one candidate. Future direction
(not implemented): diversified ETF / sector time-series trend or
relative-strength rotation.

## Reproduction commands

> Commands target the retired harness `scripts/strategy_diagnose.py` (removed 2026-08-18). The evidence above is frozen; the current validation harness is `scripts/strategy_robustness.py` + the cadence-mission scripts.

```
python scripts/strategy_diagnose.py --mode baseline --start 2020-01-02 --end 2024-12-31
python scripts/strategy_diagnose.py --mode alpha   --start 2020-01-02 --end 2024-12-31
python scripts/strategy_diagnose.py --mode alpha   --start 2025-01-02 --end 2026-07-30
python scripts/strategy_diagnose.py --mode incidents --start 2020-01-02 --end 2024-12-31
python scripts/strategy_diagnose.py --mode exposure  --start 2020-01-02 --end 2024-12-31
python scripts/strategy_diagnose.py --mode signal    --start 2020-01-02 --end 2024-12-31
python scripts/strategy_diagnose.py --mode scanner   --start 2020-01-02 --end 2024-12-31
python scripts/strategy_diagnose.py --mode benchmarks --start 2020-01-02 --end 2024-12-31
```

Artifacts under `reports/research/<mode>/` (gitignored): report.json
(summary + decisions + skips + daily), fills.csv, signal.json,
scanner_attribution.json, benchmarks.json, pnl.json, exposure_daily.csv.
