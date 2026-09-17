# Overnight / intraday alpha design report

## Design decision

`DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`

Exactly one candidate is frozen: `equity_overnight_reversal_v1`. It is eligible
for a later, separately authorized economic test only. This task did not run
that test and did not change production code, configuration, PAPER behavior, or
LIVE authorization.

## Starting main SHA

`main` and `origin/main` both resolved to
`5175a58b2fb4084e5c5a00854d3c3c94b0e819a1` at the start. The design branch
`research/overnight-intraday-design-v1` already existed at that exact SHA and
was used without altering `main`.

## Current ParayaBuck constraints

The current equity product is `equity_residual_momentum_v3`, a `1d` SPY-
referenced, weekly strategy. Its production identity is 252 formation bars,
21 skipped bars, a 273-bar beta window, 63-bar residual volatility, positive
12-1 residual gating, raw 6/9/12 percentile consensus, IVOL rejection at `0.70`,
`top_n: 8`, inverse-volatility sizing capped at `0.10`, and SPY excluded from
trading. Its maximum equity allocation is `0.55`.

The account has maximum gross exposure `0.60`, minimum cash `0.40`, maximum
position weight `0.10`, and maximum positions `6`. The equity scanner is
point-in-time and dynamic: price floor `$5`, trailing median dollar-volume
floor `$20m`, completeness `0.95`, ATR bounds `0.005`–`0.12`, spread-quality
ceiling `50` bps, supported exchanges NASDAQ/NYSE/ARCA/AMEX/BATS, maximum five
names per sector, target shortlist `300`, and maximum streamed observation set
`30`. Crypto settings are out of scope and remain unchanged. PAPER is the
configured mode; LIVE is disabled.

The current engine makes decisions on completed bars and fills resulting orders
at the next tradable bar open. An order whose creation timestamp equals a bar's
timestamp cannot fill on that bar. The current equity cost inputs are 2 bps
adverse slippage, 2 bps spread, zero commission, and effective-dated regulatory
fees, with a 0.5% rebalance band and $10 minimum order notional.

## Literature findings

| Mechanism | External evidence | Execution assumption | ParayaBuck fit |
| --- | --- | --- | --- |
| Cross-period reversal | U.S. evidence links high/low overnight moves to opposite regular-session behavior through investor clienteles, opening attention pressure, and liquidity-provider price concessions. See [Lou, Polk & Skouras](https://personal.lse.ac.uk/polk/research/TugOfWar.pdf), [Berkman et al.](https://ideas.repec.org/a/cup/jfinqa/v47y2012i04p715-741_00.html), [Akbas et al.](https://www.sciencedirect.com/science/article/pii/S0304405X21004116), and [Della Corte et al.](https://assets.super.so/e46b77e7-ee08-445e-b43f-4ffd88ae0a0e/files/c953a0e6-e93e-4bf7-b839-45a90cedced4.pdf). | The closest published trade observes the overnight move at the open and captures the following open-to-close interval; it is sensitive to opening-price and cost definitions. | Fits the desired economic distinction, but the current daily path can only implement a one-session-lagged version at the next tradable open. That lag is frozen and disclosed. |
| Same-period continuation / mixed day-night persistence | [Lou et al.](https://personal.lse.ac.uk/polk/research/TugOfWar.pdf), [Hendershott et al.](https://jhfinance.web.unc.edu/wp-content/uploads/sites/12369/2018/11/2019JH20180821_Asset-Pricing-A-Tale-of-Night-and-Day.pdf), and [Aboody et al.](https://qiniu-images.datayes.com/SSRN-id2554010.pdf) show that continuation, persistence, sentiment, and risk relations can exist in particular periods or horizons. | Often characteristic portfolios, beta sorts, or longer-horizon holdings rather than a clean next-session open-to-close implementation. | Less direct for the requested overnight-to-regular-session mechanism and too close to existing close-to-close momentum if imported into V3. Not selected. |

The focused review covered decomposition, reversal, continuation, investor
attention, liquidity providers, opening-price measurement, transaction costs,
large/liquid stocks, and illiquidity dependence. [Lu, Malliaris & Qin](https://www.sciencedirect.com/science/article/pii/S0304405X23000491)
provide a modern fast/slow-liquidity-provider explanation with evidence among
large/liquid stocks, while [Miwa](https://doi.org/10.1142/S2010139219500022)
warns that some reversal evidence is stronger in illiquid names. The review
therefore treats liquidity as a feasibility gate, not as permission to weaken
the current scanner.

## Overnight/intraday data feasibility

The local v5 package contains 11,686,832 rows and 9,010 symbols, with open,
high, low, close, volume, timestamp, market, and timeframe fields. All rows have
finite positive open and close values; there are zero duplicate symbol/session
rows. Every non-first symbol observation has a nonempty prior close, but 5,791
observations across 5,648 symbols are separated by a gap relative to the SPY
session calendar. The later test must use an explicit immediately-previous
common-session join.

SPY has 2,658 valid bars from 2016-01-04 through 2026-07-30 and covers 99.962392%
of the package's 2,659 distinct session dates. The candidate therefore uses
2026-07-30 as its common-date endpoint. There are 8,568 symbols with at least
60 bars, 7,572 with at least 252, 7,437 with at least 273, and 6,460 with at
least 500.

The raw file contains provider aliases `EQUITY`/`equity` and `1Day`/`1d`.
Because the current loader lowercases market but preserves timeframe spelling,
the later test must first create a deterministic read-only normalized view and
fail closed on canonical duplicate conflicts. The structural audit found zero
symbol/session duplicates, so this is feasible without dropping the alias-only
rows. The exact receipt is in `DATA_FEASIBILITY.json`.

## Corporate-action / open-price semantics

The v5 manifest identifies the historical feed as SIP with split adjustment.
The corporate-action inventory contains 141,908 cash-dividend rows, 270 forward
split rows, 1,207 reverse split rows, and 61 stock-dividend rows. The structural
split check found 1,364 material split-event pairs with a prior observation and
zero adjacent open/prior-close ratios within 5% of the unadjusted material split
factor. The provider-adjusted signal series and the repository's
`src/parayabuck/data/adjustment.py` contract are the basis authority; the
product loader explicitly refuses to apply split actions to already
split-adjusted prices.

The candidate's signal is a price-component signal on split-adjusted OHLC. It
does not add dividends to the score. In the later P&L experiment, explicit cash
dividends must be credited exactly once and split actions must not be applied a
second time.

The provider documentation says daily bars aggregate qualifying trades and that
the daily open is the first included regular-sale trade, not necessarily the
official exchange opening-auction clearing price ([Alpaca market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq),
[historical bars reference](https://docs.alpaca.markets/us/reference/stockbarsingle-1)).
NYSE and Nasdaq publish separate opening-auction processes ([NYSE auctions](https://www.nyse.com/trade/auctions),
[Nasdaq Open/Close](https://www.nasdaqtrader.com/Trader.aspx?id=OpenClose)).
The later test must therefore label fills `provider_daily_open_proxy`; it may
not claim MOO/LOO or auction-clearing execution from this OHLC file.

## Causality analysis

For session `d` and immediately preceding common session `p(d)`:

```text
ON_i,d   = Open_i,d / Close_i,p(d) - 1
ON_SPY,d = Open_SPY,d / Close_SPY,p(d) - 1
Score_i,d = -(ON_i,d - ON_SPY,d)
```

The overnight value becomes observable at `Open(d)`, but the current strategy
path does not decide until the completed daily bar at `Close(d)`. The frozen
candidate waits for that completed decision and submits a target that fills at
`Open(n(d))`, the next tradable bar open. It never uses the future fill open,
future high/low/volume, current status of a future lifecycle, or a same-session
close to create an order. The exact event map is in `CAUSALITY_AUDIT.md`.

This is a deliberate one-session-lagged implementation. It does not pretend to
capture the same-day `Open(d) -> Close(d)` interval used by the closest published
experiments. With daily target replacement, the engine's realized holding
interval includes the next regular session and the next opening transition;
that fact must remain visible in the later report.

## Chosen mechanism

**Overnight-to-following-session reversal, implemented with one session of
causal engine latency.**

## Exact proposed candidate

`equity_overnight_reversal_v1` is frozen as follows:

* Signal: `ON_i,d = Open_i,d / Close_i,p(d) - 1`; `ON_SPY,d = Open_SPY,d /
  Close_SPY,p(d) - 1`; score `= -(ON_i,d - ON_SPY,d)`.
* Formation horizon: exactly one overnight session.
* Market adjustment: simple contemporaneous SPY excess; no rolling beta.
* Decision: after the completed regular daily bar for `d`.
* Execution: next tradable daily-bar open `Open(n(d))` under current engine
  semantics.
* Rebalance: daily.
* Ranking: higher score is better; the most negative overnight excess moves rank
  highest.
* Selection: at most six names.
* Weighting: fixed equal target `0.55 / 6 = 0.0916666667` per selected name;
  unused capacity stays in cash when fewer than six qualify.
* Position cap: current `0.10` cap.
* Direction: long-only; SPY remains a non-traded reference.
* Universe: unchanged current point-in-time liquidity/tradability scanner,
  lifecycle rules, and maximum-30 observation policy.
* Holding/replacement: hold between daily completed-bar decisions; replace via
  net deltas at the following open; retain current sells-before-buys,
  participation, completeness, terminal-lifecycle, rebalance-band, and minimum
  notional policies.
* Costs: current 2-bps slippage, 2-bps spread, zero commission, and
  effective-dated SEC/TAF/CAT fees, with the later 1x/2x/3x stress protocol.
* Capital: no more than the current `0.55` equity allocation and no additional
  account capital or sleeve.

## Why reversal or continuation

Reversal is selected because the directly relevant U.S. evidence is more
consistent for an overnight pressure move followed by opposing regular-session
price formation than for a clean overnight-to-day continuation rule. The
attention/clientele and heterogeneous-liquidity-provider mechanisms explain why
an opening price can contain temporary pressure even when order imbalance
persists. The large-stock Berkman sample and the modern Lu–Malliaris–Qin
evidence reduce, but do not remove, the concern that this is only a microcap
effect. The current scanner and hard cost gates are the appropriate test of that
remaining concern.

## Why this differs from V3

V3 is long-horizon market-residual momentum: a rolling 273-bar beta estimate,
252/21 residual formation/skip, positive 12-1 gate, 6/9/12 rank consensus,
63-bar residual-volatility filter, inverse-vol sizing, and weekly cadence. The
candidate is a one-session price-domain signal at the market open, simple SPY
excess, contrarian ranking, six long names, fixed equal weights, no IVOL gate,
and daily cadence. It does not reuse V3's beta machinery or its signal
parameters.

## Why this is not the previously rejected residual-reversal strategy

The prior short-term residual-reversal campaign is closed and was not reopened,
read through deleted history, or used for tuning. The distinction here is
frozen from the current design itself: B ranks the latest regular-session
close-to-open component relative to SPY and is executed with the current
next-open contract. It does not use a close-to-close residual formation window,
the V3 beta/volatility pipeline, or any prior campaign result. Any conceptual
overlap is a red-team risk to be checked by definition and by the later frozen
experiment, not by mining the closed campaign.

## Future economic test design

Compare Control A (current V3) against Candidate B (the exact specification
above) as two alternative account configurations. Use the same normalized v5
dataset, PIT scanner, lifecycle treatment, SPY reference, six account slots,
0.55 equity budget, risk overlay, execution path, and cost model. Do not run
V3+B in the first experiment.

Use warmup 2016-01-04–2016-12-30, development 2017-01-03–2021-12-31, and locked
validation 2022-01-03–2026-07-30. Evaluate 1x, 2x, and 3x equity costs. The
complete test design and numerical gates are in
`FUTURE_TEST_PROTOCOL.md`; no part of it was run here.

## Pre-frozen acceptance gates

All gates are conjunctive and were written before any candidate outcome:

1. structural hashes/row count/finite-price/duplicate/PIT/session checks pass;
2. B has positive net total return and CAGR in full, development, and
   validation at 1x;
3. B beats A on at least two of Sharpe, Sortino, and Calmar by 0.05 in full,
   with no remaining metric worse by more than 0.05, and supports validation;
4. B is positive in at least three of full validation years 2022–2025, with no
   full-year loss below 8%;
5. B maximum drawdown is at most 10% and no more than one percentage point
   worse than A at 1x;
6. B has positive validation CAGR at 2x and validation total return above -2%
   at 3x, with the frozen drawdown stress limit;
7. annualized one-way turnover is at most 20x, order completion is at least
   95% within two tradable sessions, and unexplained skipped decisions are at
   most 5%;
8. the position cap holds, no symbol contributes more than 20% of absolute net
   P&L, and leave-one-symbol-out validation remains positive; and
9. capital, slot, scanner, lifecycle, and data-feed parity is preserved.

## Red-team findings

The material risks are lookahead, same-open timing, provider-open versus auction
semantics, microcap dependence, cost erosion, split/dividend handling, missing
symbol sessions, and accidental overlap with closed residual-reversal work.
They are resolved by the one-session lag, explicit provider-open label,
unchanged liquidity gates, split-adjusted/no-double-counting contract,
common-session/PIT joins, and hard cost/turnover/concentration gates. The
candidate is ready only in this narrowed form.

## Confirmation that no local economic outcome was inspected

No strategy P&L, candidate return, CAGR, Sharpe, Sortino, Calmar, drawdown,
profit factor, NAV, trade profitability, hit rate, forward-return relationship,
candidate/subsequent-return correlation, quintile/decile forward return, or
parameter-performance surface was calculated or inspected. The only price
ratio check was the explicitly requested structural split-basis QC; it was not
used as a signal or economic selection criterion. No backtest result files were
created.

## Files created

* `CURRENT_CAPABILITY_AUDIT.md`
* `LITERATURE_REVIEW.md`
* `DATA_FEASIBILITY.json`
* `CAUSALITY_AUDIT.md`
* `CANDIDATE_DESIGN.md`
* `FUTURE_TEST_PROTOCOL.md`
* `DESIGN_RED_TEAM.md`
* `DESIGN_DECISION.json`
* `FINAL_DESIGN_REPORT.md`

## Branch SHA

The immutable branch tip was verified with `git rev-parse HEAD` after the
design-only commit. Its exact value is reported in the task handoff rather than
embedded here: embedding a commit's own SHA would change that commit's SHA.
