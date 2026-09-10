# Immediate candidate freeze — 2026-09-09

Status: `FROZEN_PRE_OUTCOME`. This is a research-only definition freeze. No
candidate return, NAV, Sharpe, drawdown, turnover, bootstrap, permutation,
placebo, or V3-overlay result was used to choose the set. The existing V3,
PAPER, LIVE, product configuration, and runtime state are untouched.

The selection is deliberately limited to direct, locally available daily
equity OHLCV inputs plus the existing PIT identity and corporate-action
records. It does not use OHLCV as a substitute for SEC events, fund flows,
borrow, futures, funding, or intraday execution data. Those branches are
excluded because they cannot produce an honest immediate full/recent/holdout
test from the current files.

The canonical machine-readable freeze is
`data/research_manifests/immediate_candidate_freeze_2026_09_09.json`.

## Available inputs

`equity_pit_symmetric_2016_2026_v5.csv` has the exact fields
`timestamp,symbol,timeframe,open,high,low,close,volume,market`, covers
`2016-01-04` through `2026-07-31`, and contains split-adjusted daily OHLCV.
The pinned inventory records 11,686,832 rows and 9,010 symbols. Entity
lifetime and corporate-action files provide identity, terminal, split, and
dividend treatment. The dated PIT eligibility intervals provide the
decision-time universe gate. Signals use the canonical split-adjusted bars;
there is no raw/adjusted pair in that input and dividend-adjusted prices cannot
be mixed into a signal. Corporate-action rows affect outcome cash, split, and
terminal treatment only when their ex-date is at or before the frozen cutoff;
later rows are quarantined.

The V3 reference artifact has 2,658 daily NAV rows, 495 dated weight
decisions, 1,890 fills, and 2,656 PIT snapshots. The overlay reads these as a
reference only: every V3 weight and decision remains preserved.

## Frozen candidates

| ID | Name | Signal and direction | Rebalance / holding | Required direct fields |
|---|---|---|---|---|
| A01 | `equity_low_realized_vol_22_v1` | `stdev(close_t/close_(t-1)-1)` over 22 completed sessions; ascending | Monthly / 21 sessions | close |
| A02 | `equity_short_reversal_5_v1` | `close_t/close_(t-5)-1`; ascending | Weekly / 5 sessions | close |
| A03 | `equity_volume_surprise_20x60_v1` | `median(close*volume,20) / median(close*volume,60)`; descending | Monthly / 21 sessions | close, strictly positive volume |
| A04 | `equity_log_price_slope_126_v1` | OLS slope of log close over 126 sessions; descending | Monthly / 21 sessions | close |
| A05 | `equity_52w_breakout_distance_252_v1` | `close / max(prior 252 highs) - 1`; descending | Monthly / 21 sessions | high, close |
| A06 | `equity_range_efficiency_20_v1` | `abs(close_t-close_(t-20)) / sum(abs(close_i-close_(i-1)))`; descending | Monthly / 21 sessions | close |

All six use the same V3-compatible PIT equity universe, exclude SPY, use
`top_n=8`, and use equal weights capped at 10% per name inside a 55% equity
sleeve. A symbol is omitted for a decision if its required observations are
missing, nonfinite, nonpositive where required, not aligned, or not PIT
eligible. If fewer than eight valid names remain, the sleeve is cash for that
decision. No forward fill or zero-return replacement is allowed.

For A03, a zero-volume bar is treated as missing for the 60-session window.
All 60 volume observations must be finite and strictly positive, so the VS
denominator is never silently zero.

These mechanisms are independent of V3's residualized 12-1 return gate,
6/9/12 rank consensus, market-beta residual construction, skip window, and
inverse-residual-volatility sizing. A03 is an alpha candidate based on volume
surprise; it must not be relabeled as a liquidity control after results are
seen.

## Test protocol frozen now

The cutoff is `2026-07-31T23:59:59Z`. The windows are full
`2022-01-01..2025-12-31`, recent `2024-01-01..2025-12-31`, and untouched true
holdout `2026-01-01..2026-07-31`. Warmup observations may precede each window,
but no holdout observation may affect formula, direction, universe,
parameter, cadence, or candidate selection.

Each candidate is evaluated with the fixed lag grid of 1, 3, and 7 supported
trading sessions, the fixed cost multipliers 1x, 2x, and 3x, and the fixed
rebalance/holding definitions above. Decisions use completed bars and fills
use the next supported NYSE regular-session open. Current V3 execution,
regulatory fee, terminal, and risk settings are measurement policy only. For
each scheduled rebalance, the feature cutoff is shifted back by the selected
1/3/7-session lag; the target still fills only at the next supported open,
with no same-bar fill or overlapping target. At a rebalance, the prior
candidate target is closed and at most one new target per symbol is opened;
the holding interval runs from fill to the next scheduled rebalance.

Every cell must report standalone return, CAGR, Sharpe, Sortino, turnover,
fees/slippage/spread, maximum drawdown, worst day, CVaR05, calendar-year and
declared-regime means, exact-date candidate/V3 correlation, exact-date
selected-name overlap when dated V3 weights are present, and finite
accounting/terminal reconciliation. Missing dated V3 weights make overlap
unavailable; it is never inferred from NAV. The robustness pass is fixed at 10,000
21-session moving-block bootstrap resamples (seed `20260909`), 10,000
within-window paired-sign permutations (seed `20260910`), and a +7 supported
trading-session schedule placebo. The placebo is diagnostic only.

Turnover is gross traded notional divided by lagged candidate-sleeve NAV. Each
trade ledger must retain decision time, signal-as-of time, fill time, symbol,
pre/target/filled weights, quantity, notional, fee, slippage, and spread. A
missing or nonfinite ledger field blocks the affected cell; no cost or
turnover is imputed.

The shared-capital test is fixed at `95% unchanged V3 + 5% candidate`. It may
use only dated spare capacity while preserving V3 weights, with equity `<=55%`,
crypto `<=5%`, gross `<=60%`, cash `>=40%`, at most two crypto positions, and
at most six total positions. Candidate holdings are explicitly capped at
`min(top_n, 6 - dated V3 holding count)` and become zero when no slot exists.
Missing V3 state or capacity makes that date ineligible; no capacity is
invented. The V3 reference ends at 2026-07-29, so missing 2026-07-30/31 state
is not carried forward. No candidate can alter V3, integrate,
promote, or enter PAPER/LIVE through this freeze.

The machine-readable artifact contains the complete input hashes, V3 pins,
formulae, direction, lag/hold/cadence, missing-data, cost, robustness,
overlay, and admission contracts. Its `protocol_sha256` is the canonical
hash of the manifest with that self field removed.
