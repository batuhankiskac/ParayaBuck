# Frozen protocol: `equity_short_term_residual_reversal_v1`

Frozen on 2026-09-15 before any candidate or treatment economic result was
calculated. The baseline is local `main` SHA
`8123694a71d8fbb0e520f13b4e1692e932754e97`. This is one hypothesis test, not a
parameter search.

## Candidate contract

| Field | Frozen value |
| --- | --- |
| Identity | `equity_short_term_residual_reversal_v1` |
| Market / signal | US equities / daily (`1d`) |
| Benchmark | `SPY` |
| Residualization | exactly the current V3 timestamp-aligned simple-return and rolling OLS-beta semantics |
| Beta window | current V3 `273` aligned returns (`274` completed closes where available) |
| Formation | exactly `21` completed trading-session residual returns |
| Skip | exactly `0`; the latest completed residual return is included |
| Score | compounded residual return over that exact 21-return window; more negative ranks stronger |
| Direction | long-only |
| Rebalance | weekly, using the current equity decision schedule |
| Selection | exactly `top_n=6` |
| Positive-score gate | none; all finite, eligible scores may rank, so negative scores can be selected |
| Ivol rejection | current V3 semantics, unchanged, `ivol_reject_quantile=0.70` |
| Weighting | current inverse annualized residual-volatility weighting |
| Residual-vol window | current V3 `63` residual returns |
| Position cap | current production `0.10` per position |
| Additional indicators | none |

No RSI, Bollinger Bands, MACD, moving-average, volume-confirmation, VIX,
earnings, news, ML, or regime-switching input is permitted.

## Data, chronology, and representation

The first-choice local dataset is the compatible v5 panel
`data/historical/equity_pit_symmetric_2016_2026_v5.csv`; no old research
result is an input. Provider spellings `1d`/`1Day` and `equity`/`EQUITY` are
normalized deterministically. Explicit zero placeholder bars are excluded
from the research panel; positive rows must satisfy finite prices, positive
close, `high >= max(open, close)`, and `low <= min(open, close)`. Duplicate
`(symbol,timestamp)` bars fail closed. The panel is split-adjusted and uses
cash dividends from the matching v5 corporate-action table; stock/split
actions are not applied a second time.

The preferred windows are fixed before results:

* warmup: 2016-01-04 through 2016-12-30;
* development: 2017-01-03 through 2021-12-31;
* validation: 2022-01-03 through 2026-07-31, ending at the last actually
  available common SPY session if the panel has no SPY bar on 2026-07-31.

The research dataset is broad and point-in-time by the supplied provider
entity-lifetime metadata. The final report must state any provider coverage,
delisting, ticker-identity, metadata, or last-session limitations rather than
silently treating them as exchange-complete.

## Common universe and engine

Candidate, V3, and treatment use the same freshly rebuilt point-in-time
production scanner universe, same normalized panel, same split-adjusted
representation, same dividend treatment, same completed-bar decision boundary,
same next-tradable-bar open fills, same participation model, and same current
risk/execution settings. No old result numbers or old research branches are
used.

Standalone reversal is run at the current maximum equity sleeve allocation of
`0.55` plus cash, at 1x/2x/3x costs. V3 is freshly run at the same `0.55` plus
cash for the exact same dates.

## Capital-neutral treatment

The treatment allocation is frozen as:

* V3 sleeve: `0.4125` (`0.75 × 0.55`);
* reversal sleeve: `0.1375` (`0.25 × 0.55`);
* total equity sleeve maximum: `0.55`;
* remaining account balance: cash; crypto is unchanged and excluded.

Each component first retains its own strategy-produced inverse-vol weights and
is capped at its `0.55` reference sleeve budget without renormalizing a thin
or cash/risk-off result. It is then multiplied by `0.75` or `0.25`. For each
symbol, the shared desired target is the sum of the two scaled sleeve targets.
Overlapping symbols therefore have one deterministic target and one net order.
The shared current risk overlay then applies, without alteration, the existing
per-position cap, six-position cap, total `0.55` allocation/cash buffer, gross
limit, daily-loss behavior, and order band/notional floor. For the shared
position-count cap, composite targets are intentionally unranked (`rank=0`),
so the existing overlay's deterministic target-weight descending order (then
symbol) decides which names survive; this is the declared capital-composition
rule, not a tuned alpha filter.

## Predeclared gates

The verdict is based on net results and is one of the three exact strings in
the brief. Gates are frozen before the treatment result:

1. At 1x, reversal full-period net CAGR is positive and validation net return
   is positive.
2. At 2x, reversal full-period net return remains positive; at 3x there is no
   catastrophic collapse.
3. Full-period standalone daily return correlation with V3 is `< 0.75`.
4. Treatment versus `0.55 V3` improves at least two of CAGR, Sharpe, Sortino,
   and Calmar without materially worsening the others.
5. Treatment maximum drawdown is no worse than V3 by more than 2.0 percentage
   points.
6. In validation, treatment Sharpe or CAGR exceeds V3 and validation maximum
   drawdown does not deteriorate by more than 2.0 percentage points.
7. Incremental turnover/cost does not consume essentially all measured
   improvement.
8. The result is not overwhelmingly dependent on one symbol or isolated event.

No parameter or allocation may change after development or validation results
are observed. Concentration and leave-one-symbol-out calculations are
diagnostics only and cannot alter the frozen strategy.
