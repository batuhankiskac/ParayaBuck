# Strategy queue baseline contract — 2026-09-06

Status: read-only baseline refresh for the 34-card research queue. No new
economic run, production/config change, registry change, data download, or
PAPER/LIVE action was performed.

## Current native baseline

The fixed equity identity is `equity_residual_momentum_v3`: long-only, weekly,
positive 12-1 residual gate, raw 6/9/12-month rank consensus, idiosyncratic
volatility rejection at q=0.70, top eight names, inverse-volatility weights
capped at 0.10 each. Its configured allocation ceiling is 0.55.

The fixed crypto identity is `crypto_low_variance_hysteresis_v1`: long-only
spot crypto, 22 completed daily returns from 23 consecutive daily prices,
ascending low-volatility rank, HYST4 retention (a held symbol remains only when
PIT-eligible and rank <=4), top two equal weights, and 96-hour decisions. Its
configured allocation ceiling is 0.05.

The account contract is initial cash 100,000, gross <=0.60, cash >=0.40, max
six positions, max single position 0.10, max crypto exposure 0.10, max crypto
position 0.05, and max two crypto positions. These are ceilings, not guaranteed
realized exposure. The current configuration keeps LIVE disabled.

Sources: `config/settings.yaml:141-193`,
`src/parayabuck/portfolio/equity_residual_momentum_v3.py:1-31,152-186`,
`src/parayabuck/portfolio/crypto_low_variance_hysteresis.py:22-119`.

## Timing, costs, and point-in-time rules

The native engine makes decisions after completed bars and fills pending orders
at the next tradable bar open. Sells run before buys at the same timestamp.
At 1x the configured model is 2 bps slippage plus 2 bps quoted spread (one-half
spread in the adverse fill price), 25 bps crypto taker fee per side, and zero
commission. The 2x and 3x research tiers multiply slippage, spread, and crypto
fee. The engine also uses known prior 20-bar average volume with 10%
participation, a 0.5% rebalance band, and a $10 minimum order notional.

Crypto research starts with raw 1h OHLCV, aggregates only complete contiguous
24-hour daily buckets, applies a +24-hour signal shift, and uses zero-yield
cash. Missing or insufficient history makes a symbol ineligible. The scanner
and observation set are PIT and configured-pool constrained; dataset-only
symbols cannot silently become alpha candidates.

The crypto source has 705,541 rows, 25 symbols, and fields
`timestamp,symbol,open,high,low,close,volume`, spanning
`2021-01-01T06:00:00Z` to `2026-07-31T23:00:00Z`. It is configured-pool OHLCV
with scanner and cost proxies, not a venue-PIT bid/ask history. No post-
`2026-07-31` holdout is present.

Sources: `src/parayabuck/backtest/portfolio_engine.py:228-265,752-768`,
`src/parayabuck/data/aggregator.py:124-160`,
`src/parayabuck/backtest/universe.py:195-217,288-319`,
`config/settings.yaml:82-133,185-228`.

## Long-history safety gate

`PortfolioBacktestEngine._build_context` trims ordinary symbols to
`max(strategy.minimum_history_bars, 274)` while exempting BTC. A new candidate
must declare its real required history and prove that every required symbol
receives that history in the actual multi-symbol context before any economics.
The former P364 screen declared 23 bars and therefore supplied BTC=364 but
ordinary symbols=274; that result is retained as an invalid algorithm baseline.
The research-only corrected adapter changes only `minimum_history_bars` to 364
and keeps the score, HYST4/top-2/96h policy, costs, shift, and execution fixed.

## Native joint-capacity boundary: incumbent + corrected P364

`run_product_backtest` constructs one strategy and one
`PortfolioBacktestEngine` per call. It cannot hold two independently scored
crypto sleeves with independent HYST state in one native call. Passing the
incumbent's combined holdings to the candidate would contaminate retention
state; averaging standalone NAV curves would omit shared cash, order
competition, fees, partial fills, and the max-two-position rule.

The safe research seam is one research-only composite implementing the existing
`PortfolioStrategy` contract. It must score incumbent and corrected P364 on
the same completed-bar context, keep their virtual holdings/state separate,
scale each sleeve to 0.025, merge duplicate symbols deterministically, and
submit one target set to one native engine. It must fail closed when the merged
union exceeds two crypto positions, rather than silently evicting a target.
The single ledger then applies the existing cash/gross/position limits, sell-
before-buy ordering, next-open fills, actual costs, and final-mark semantics.
No such composite was implemented or run here.

Use `run_product_backtest(..., persist=False)` or the existing corrected runner
with `state_path=None` for isolated research. Existing output artifacts should
be reused instead of rerunning: the attempt-03 screen shards/analysis/audit,
the corrected P364 results and audit, their frozen manifests, and the existing
assessment/trace/exposure reports.

## H14 local feasibility: turn-of-month ETF pilot

An honest isolated **daily next-open preflight** is feasible with existing local
inputs while leaving V3 unchanged, subject to a research-only fixed ETF adapter
and a frozen turn-of-month rule. This is a data-path result, not permission to
report economics: the economic pilot remains gated until the preflight records
the exact input identities, cutoff, corporate-action mapping, and executable
next-session rows.

| Input | Current evidence | Limitation |
|---|---|---|
| SPY, QQQ | `data/historical/equity_pit_symmetric_2016_2026_v5.csv` (SHA-256 `bb7aaae8...`), 2,658 daily 1d OHLCV rows each, 2016-01-04..2026-07-30 | No intraday bars; the canonical file is 9,010-symbol v5 and must be filtered to the frozen ETF universe |
| IWM | `data/research/generated/m3_data_gate_r4/iwm_normalized.csv` (SHA-256 `8f8122e3...`), 2,658 daily split-normalized SIP 1d OHLCV rows, same range | Separate sidecar; it is backed by the captured Alpaca SIP raw bundle (`9590e35d...`) and `m3-iwm-normalizer-v1`, but is absent from `equity_1200_2016_2026.csv` |
| Raw IWM provenance | `data/research/external/m3_iwm_raw_2026_09_r4/` with source receipt and one page, HTTP 200, 2,658 rows, request `IWM`, `1Day`, `sip`, `split`, `[2016-01-01,2026-07-31)` | Raw pages are local/untracked; consume only the hash-pinned captured bytes; no fallback or synthesis |
| Corporate actions | `data/historical/equity_survivorship_full_corporate_actions_v5.csv` (SHA-256 `a0a60755...`), 143,446 rows; 141,908 cash dividends, 61 stock dividends, 270 forward splits, 1,207 reverse splits | Apply only actions available by the decision/evaluation cutoff; filter the future-dated rows |
| Calendar | M3 gate records 2,658 NYSE sessions and `pandas_market_calendars` NYSE 5.4.0 | The local ETF inputs are daily, not intraday |

The older research helper still defaults to
`data/historical/equity_1200_2016_2026.csv` plus
`data/historical/equity_1200_dividends.csv` (`scripts/research_helpers.py:38-46`).
That is a separately hashable legacy input pair (`3baec12e...` and
`7d79025d...`); a preflight must choose and record one coherent pair rather
than mix legacy bars with v5 corporate actions by convenience.

The pilot can therefore use a frozen SPY/QQQ/IWM universe, daily calendar
classification of the last trading day and first three trading days of each
month, and native next-session-open execution. The source contract is
split-adjusted bars (`adjustment=split`) with dividends kept as separate cash
actions; apply those cash flows exactly once and do not add another split
adjustment. The product engine's honest fill is a completed-bar decision and
the next tradable daily bar's `open`, with adverse slippage and spread. It must
not claim classic close fills, quote-level opening truth, or intraday timing.
The current product config does not select these three references as a traded
equity universe, so the pilot needs an explicit research PIT universe/adapter;
no production universe or V3 change is implied.

The available `open` field proves a daily-bar next-open model can be replayed;
it does not prove the exact exchange auction/open execution price. A frozen
SPY-only adaptation can be run only after verifying, for every signal, that a
later NYSE session row exists. An order created on the final `2026-07-30`
session has no in-window next bar and must remain unfilled, consistent with the
native contract.

The SPY corporate-action mapping is partly verified: the 40 v5
`cash_dividends` rows match the 40 legacy `equity_1200_dividends.csv` rows
exactly on `ex_date -> timestamp[:10]` and `rate -> value`. Renaming those
fields is a lossless schema adaptation and does not invent payable dates. It
does not by itself clear the economics gate. Fourteen of the 40 v5 rows have
no `payable_date`, but payable date is a cash-update field and is not needed to
establish ex-date entitlement. The provider defines ex-date as the date when a
purchase no longer receives the action, while a sale on or after ex-date keeps
the entitlement ([Alpaca corporate-action announcement fields](https://docs.alpaca.markets/us/reference/getcorporateannouncements)).
The native engine applies actions before the pending bar-open fill
(`src/parayabuck/backtest/portfolio_engine.py:514-517`) and credits only
positions already present, so an ex-date-open buy is excluded and an ex-date
sale retains the dividend. A normalized `ex_date -> UTC timestamp` and
`rate -> value` mapping is therefore a routine lossless schema adaptation for
this open-fill contract, provided the pilot records cash accrual at ex-date
(not a fabricated payable date), preserves raw IDs, and proves split-adjusted
prices are not adjusted a second time. H14 remains preflight-pending until
that isolated preflight records those mappings and hashes; the local data path
itself is not synthetic or absent.

This is data-feasible, not evidence of an economic edge. The McConnell–Xu
turn-of-month paper reports a historical calendar effect, but its result does
not validate this ETF implementation, current costs, or complementarity to V3.

## Retained artifacts

- `data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt03_frozen.json`
- `data/research/portfolio_expansion_crypto_screen_2026_09/attempt_03_canonical/`
- `data/research_manifests/high_proximity364_history_fixed_2026_09_05.json`
- `data/research/high_proximity364_history_fixed_2026_09_05/results.json`
- `data/research/high_proximity364_history_fixed_2026_09_05/audit_2026_09_05.json`
- `docs/research/high_proximity364_final_assessment_2026_09_05.md`
- `docs/research/portfolio_expansion_batch_2026_09_05_report.md`

The corrected P364 result remains research-only and supports passive shadow
observation only. No product integration, veto, ensemble, or automatic capital
allocation is supported by these retrospective artifacts.
