# ParayaBuck V2 Validation — equity_long_horizon_momentum_1d_v2

Status: research evidence record. All numbers reproducible via
`scripts/strategy_diagnose.py` (harness retired 2026-08-18 with the rejected
implementations; evidence frozen in this document — see
`docs/rejected_strategies.md`); commands at the end. This document records
both what was tested and the exact protocol used, including its imperfections
(see "Protocol disclosure" below).

## Strategy specification (final candidate)

```
name:                 equity_long_horizon_momentum_1d_v2
market:               US equities
universe:             existing production tradability scanner (unchanged;
                      scanner attribution showed it improves signal quality)
momentum_window:      252  (12-month formation)
momentum_skip:        21   (1-month skip; short-term reversal exclusion)
trend_window:         200  (stock absolute trend: close > SMA200)
use_stock_trend:      true
use_market_regime:    false  (REMOVED — ablation evidence, see below)
sizing:               equal (1/N capped at max_single_weight=0.10)
use_vol_target:       false (REMOVED — ablation evidence, see below)
volatility_window:    20
top_n:                6
rebalance_frequency:  monthly
exclude_symbols:      [SPY]
execution:            shared PortfolioExecutionPolicy (band 0.005, min notional 10)
costs:                production model (slippage 2bps, spread 2bps,
                      effective-dated regulatory fees)

Starting-hypothesis spec (M3, as prescribed by the mission): identical to the
above EXCEPT use_market_regime=true and use_vol_target=true.
  spec hashes (sha256 of the canonical JSON parameter dicts, recorded in
  reports/research/v2_spec.json BEFORE the new-data folds were opened):
  M3 starting hypothesis: dfa36f27d6e419a07dab66836c8fc5ae78b3b9a70573589cb17bd6900c421757
  M1 final candidate:     59d0770ac1f03412454ee1eb5ce0a07fcdff5ff92403dfbd6689ae98cabe0c94
```

**Why each component exists**

- 252d formation + 21d skip: classic cross-sectional momentum (Jegadeesh &
  Titman 1993): winners over 12 months, excluding the most recent month,
  tend to keep winning over the next 3-12 months.
- Stock SMA200 trend gate: time-series momentum / trend filter literature
  (Moskowitz, Ooi & Pedersen 2012; the "trend factor") — avoids holding
  stocks in their own downtrends.
- SPY regime gate (tested, then REMOVED): market-state conditioning
  (Daniel & Moskowitz 2016). Ablation evidence rejected it for this
  universe/period (below).
- Volatility sizing (tested, then REMOVED): Moreira & Muir (2017). Ablation
  showed it only traded Sharpe for lower return; the final candidate keeps
  the simpler equal-weight construction.
- Monthly rebalance: matches the 1-month holding-period literature and keeps
  turnover/costs low.

## Protocol disclosure (how the evidence was produced — including mistakes)

1. The 2016-2019 walk-forward folds were opened TWICE. First with the M3
   starting hypothesis (the mission's fixed spec, including SPY regime +
   vol sizing); then, after the development-window component ablation
   showed the regime gate was value-negative, the final candidate (M1) was
   frozen and the folds were re-run ONCE with M1. Both fold runs are
   reported below. No parameter was ever selected using the folds' results;
   M1 was chosen purely on development-window (2020-2024) evidence. The
   re-run made the folds slightly better (M3 total -1.9% -> M1 +1.6%), so
   no favorable cherry-picking occurred — but the protocol is disclosed,
   not hidden.
2. Early V2 runs in this mission were executed with a harness parameter-merge
   bug (an empty parameter dict fell back to the configured V1 parameters,
   silently running V2 with weekly/60d/150-window settings). All such runs
   were identified, discarded, and re-run after the fix. Every number in
   this document comes from post-fix runs (verified by the recorded
   strategy_parameters in the artifacts).
3. The development-window ablation was run in two generations: first against
   the 2019-only CSV (insufficient 274-bar warmup -> V2 could not trade
   before ~Aug 2020), then corrected against the extended 2016-2026 CSV.
   Only the corrected numbers are reported.

## Data boundaries

```
development (seen, diagnostic only): 2020-01..2024-12 — used ONLY for
  component ablation and parameter neighborhood; no parameter selection from
  the new-data folds.
new-data walk-forward folds (genuinely new, never inspected before this
  mission): extended daily history 2016-01..2019-05-27
  dataset: data/historical/equity_1200_2016_2026.csv
  sha256: 3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a
  (concatenation of the 2019-2026 CSV sha256
   20bb2fb58ac8e929cde2d24fad1af9b01c9f2bc98cd652fc53f1aa18728fabfd and the
   new backfill data/historical/equity_1200_2016_2019.csv sha256
   a3825dee21c76c2ae5407be6e1b54e13209c39cbe4642efdeaf82ca6f59e15ca:
   1,738,468 rows, 1,194 symbols, 2016-01-04..2026-07-30; 504 of the 1,194
   symbols have 2016-2019 bars (the rest listed later))
fold boundaries (frozen BEFORE the folds were opened):
  F1 2017-01-02..2017-12-29 (warmup 2016)
  F2 2018-01-02..2018-12-31 (warmup 2016-2017)
  F3 2019-01-02..2019-05-27 (warmup 2016-2018)
final unseen historical block: NONE — the 3.4 years of new data shrink to
  ~2.3 years of tradable time after the 13-month (274-bar) warmup; holding
  out a separate block would leave <1.5 years for folds. Say:
  NO CLEAN NEW HOLDOUT AVAILABLE
```

## Component ablation (development window 2020-2024, alpha-diagnostic path,
extended dataset, monthly, post-fix runs)

| Variant | Return | Sharpe | maxDD | trades | turnover | avg expo |
|---|---|---|---|---|---|---|
| M0 momentum only (equal w) | +105.6% | 1.02 | 16.2% | 123 | 20.7 | 50.3% |
| M1 + stock SMA200 trend | **+107.0%** | **1.06** | 16.1% | 121 | 20.4 | 49.3% |
| M2 + SPY regime gate | +70.7% | 0.82 | 18.2% | 116 | 19.4 | 39.2% |
| M3 + vol sizing (full starting spec) | +68.3% | 0.92 | 16.1% | 116 | 18.9 | 35.3% |

By year, M1 vs M2 (the regime gate's effect): 2020 equal (+21.7%), 2021 equal
(+11.0%), 2022 M1 -7.8% vs M2 -13.1% (gate whipsaws in the bear year), 2023
M1 +5.8% vs M2 +3.3%, 2024 equal (+42.0%). The SPY regime gate never helped
in any year and hurt in both years it fired; it was removed per Phase 10.
Vol sizing (M3 vs M2) improved Sharpe 0.82 -> 0.92 at lower exposure but did
not add return; the final candidate is M1.

## Walk-forward results

### Final candidate M1 (frozen after ablation; single post-freeze run)

| Fold | Return | CAGR | Sharpe | Sortino | maxDD | Calmar | trades | turnover | avg expo | SPY | vol-matched SPY | excess vs SPY |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 2017 | +3.02% | 3.0% | 0.39 | 0.41 | 5.14% | 0.59 | 26 | 3.9 | 42.1% | +18.6% | +30.1% | -15.6pp |
| F2 2018 | -4.98% | -5.0% | -0.30 | -0.39 | 16.92% | -0.29 | 29 | 4.4 | 49.6% | -6.9% | -5.0% | +2.0pp |
| F3 2019H1 | +3.80% | 9.4% | 1.20 | 1.54 | 3.74% | 2.50 | 14 | 2.1 | 43.6% | +13.1% | +4.9% | -9.3pp |

Aggregates: median fold return +3.02%; median fold Sharpe 0.39; positive
folds 2/3; worst fold F2 2018 (-4.98%); best fold F3 2019H1 (+3.80%); total
walk-forward return +1.60% over 2.4 years (geometric). Fold profit
contribution (sum of fold returns in pp = +1.84pp): F3 contributes 206% of
the pp sum, F1 164%, F2 -270% — i.e. the two positive folds fully offset the
negative fold; no fold is robust and the sum barely clears zero.

### Starting hypothesis M3 (opened first; disclosed for completeness)

| Fold | Return | Sharpe | maxDD | avg expo | excess vs SPY |
|---|---|---|---|---|---|
| F1 2017 | +3.36% | 0.44 | 5.03% | 41.4% | -15.2pp |
| F2 2018 | -4.86% | -0.36 | 15.69% | 39.6% | +2.1pp |
| F3 2019H1 | -0.21% | -0.03 | 3.81% | 29.2% | -13.3pp |

M3 total walk-forward -1.9%; 1/3 positive folds. The regime gate that hurt on
development data also hurt on the new-data folds.

**Reading:** neither variant beats SPY in either up-market fold; both roughly
match risk-matched SPY only in the 2018 bear fold; M1 loses to risk-matched
SPY in F1 by 27pp. There is no consistent portfolio-level edge in the
genuinely new data.

## Signal-level diagnostics (V2 score, scanner-active universe, weekly
cross-sections — the rank-IC view, distinct from the portfolio view)

| Window | 5d IC (hit) | 21d IC (hit) | 63d IC (hit) | top-6 excess vs universe (21d/63d) | top-6 excess vs SPY (21d/63d) |
|---|---|---|---|---|---|
| dev 2020-2024 | +0.048 (55%) | **+0.072** (58%) | **+0.099** (60%) | +1.1% / +3.0% | +0.7% / +2.2% |
| new data 2017-2019H1 | +0.005 (46%) | **+0.072** (56%) | **+0.158** (67%) | -0.3% / -0.3% | -0.2% / -0.1% |
| SEEN 2025-2026 | +0.035 (51%) | **-0.014** (43%) | +0.015 (59%) | +1.7% / +4.9% | +0.7% / +2.1% |

By year (21d IC): dev: 2020 +0.093, 2021 -0.024, 2022 +0.120, 2023 +0.022,
2024 +0.181; new data: 2017 +0.016, 2018 +0.087, 2019H1 +0.307 (n=8);
SEEN: 2025 -0.064, 2026 +0.142.

**Interpretation:** the long-horizon V2 score has a moderately positive rank
IC on development AND on the new 2016-2019 data (21d ~+0.07, 63d ~+0.10-0.16),
stronger than V1's short-horizon score (21d ~+0.01). But the top-6 long-only
portfolio excess over the universe median is ~0 on the new data and negative
vs SPY in up markets. Positive rank IC does not convert into long-only
top-6 excess returns — the monetization step fails. This is why the
portfolio walk-forward is flat despite a positive signal: the IC is carried
by the cross-section (losers rank low), not by top-N selection beating the
median, and the long-only book never catches SPY's winners in up markets.

## Parameter neighborhood (development window only — never applied to the
new-data folds)

| Variant | Return | Sharpe | maxDD |
|---|---|---|---|
| momentum 252, full spec (M3) | +68.3% | 0.92 | 16.1% |
| momentum 189 | +85.3% | 1.15 | 15.1% |
| top_n 8 | +29.7% | 0.67 | 12.1% |
| vol target 0.08 | +49.8% | 0.96 | 13.9% |

Sensitivity is wide (Sharpe 0.67-1.15 across lookback, top_n, and vol target)
— no stable plateau, consistent with a weak hypothesis.

## Cost stress (final candidate M1, development window, alpha path)

```
1x: +107.0% / Sharpe 1.06     2x: +83.8% / 1.02     3x: +82.7% / 1.01
```

Cost-robust (monthly rebalance keeps turnover ~4x/yr); costs are not the
binding constraint.

## Seen 2025-2026 stress (SEEN_DIAGNOSTIC_2025_2026 — diagnostic only, not OOS)

- M1 final candidate: +14.2% / Sharpe 0.66 / maxDD 18.6% / 50.1% exposure vs
  SPY +26.9% (excess -12.7pp). Positive but trails SPY; maxDD 18.6% is high
  for a ~10%-vol book (no vol targeting in the final candidate).
- M3 starting spec (corrected run): -0.50% / Sharpe 0.03 / maxDD 13.1% /
  37.1% exposure — the SPY regime gate additionally sat out the 2025 rally.
- Production policy (latches on), M1, dev: +35.1% / Sharpe 0.66 / avg
  exposure 18.6% with 40 halted decisions (39 drawdown-halt + 1 daily-loss)
  — the operational latch suppresses V2 exactly as it does V1 (unlatched
  +107%).

## Known limitations

- Survivorship: the 2016-2019 backfill uses the 1,194-symbol membership of
  the existing CSV (504 had data in 2016-2019); delisted names are absent.
  The walk-forward universe is biased toward survivors.
- Scanner in the backfill has no spread/quote data (neutral 0.5 score) and
  sector metadata is static-map-only; live scanner ranking differs on the
  spread dimension.
- The old 2025-2026 period is seen; reported only as a stress test.
- No clean holdout beyond the walk-forward folds (data too short).
- The folds were opened twice (M3 then M1) as disclosed above; the M1 runs
  are the final-candidate evidence.

## Verdict

```
V2 NOT VALIDATED.
  - Component ablation (dev): only the stock SMA200 trend added value; the
    SPY regime gate was value-negative and removed; vol sizing removed.
  - Signal: positive rank IC on dev AND new data (21d ~+0.07, 63d
    ~+0.10-0.16), but top-6 long-only excess ~0 — the signal does not
    monetize into portfolio excess.
  - Walk-forward on genuinely new 2016-2019 data (final candidate M1):
    2/3 positive folds, median fold Sharpe 0.39, total +1.6% over 2.4 years,
    negative excess vs SPY in both up-market folds, negative vs risk-matched
    SPY in 2 of 3 folds.
  - The long-horizon momentum hypothesis does not clear the bar on new data.
```

## Reproduction commands

> Commands target the retired harness `scripts/strategy_diagnose.py` (removed 2026-08-18). The evidence above is frozen; the current validation harness is `scripts/strategy_robustness.py` + the cadence-mission scripts.

```
python scripts/strategy_diagnose.py --mode alpha --strategy equity_long_horizon_momentum_1d_v2 \
  --param use_stock_trend=true --param use_market_regime=false --param sizing=equal \
  --param use_vol_target=false --data data/historical/equity_1200_2016_2026.csv \
  --start 2017-01-02 --end 2017-12-29          # F1 (and F2/F3 by dates)
python scripts/strategy_diagnose.py --mode alpha --strategy equity_long_horizon_momentum_1d_v2 \
  --param use_stock_trend=true --param use_market_regime=false --param sizing=equal \
  --param use_vol_target=false --data data/historical/equity_1200_2016_2026.csv \
  --start 2020-01-02 --end 2024-12-31          # dev ablation baseline (M1)
python scripts/strategy_diagnose.py --mode signal --strategy equity_long_horizon_momentum_1d_v2 \
  --param momentum_window=252 --param momentum_skip=21 --param trend_window=200 \
  --data data/historical/equity_1200_2016_2026.csv --start 2017-01-02 --end 2019-05-27
python scripts/backfill_equity_history.py --start 2016-01-01 --end 2019-05-27  # data extension
```
