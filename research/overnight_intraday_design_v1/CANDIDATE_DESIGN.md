# Frozen candidate design

## Candidate identity

`equity_overnight_reversal_v1`

This is one long-only alternative equity-selection hypothesis. It is not an
additional sleeve and it is not a reopening of any closed research campaign.

> After a completed regular session, buy the liquid point-in-time U.S. equities
> whose latest completed overnight move was most negative relative to SPY. The
> hypothesis is that opening pressure is partially unwound in the next regular
> price-formation interval and that the effect is persistent enough to survive
> a one-session execution delay and realistic costs.

The one-session delay is mandatory because the current engine cannot make a
daily strategy decision at the same session's open. It is part of this frozen
hypothesis, not a future tuning choice.

## Exact signal definition

Let `d` be a common regular NYSE/SPY session, and let `p(d)` be the immediately
previous common NYSE/SPY session. For stock `i`, use the split-adjusted regular-
session daily OHLC series after the declared alias normalization and lifecycle
join:

```text
ON_i,d   = Open_i,d / Close_i,p(d) - 1
ON_SPY,d = Open_SPY,d / Close_SPY,p(d) - 1
X_i,d    = ON_i,d - ON_SPY,d
Score_i,d = -X_i,d
```

The signal is price-component based. It does not add cash dividends to the
signal, use high/low/volume, estimate a beta, or use any close-to-close
formation return. A cash dividend is a later P&L accounting item and must not
be counted both in the signal and in the later portfolio accounting.

## Frozen parameters and rules

| Field | Frozen value |
| --- | --- |
| Formation horizon | Exactly `1` prior overnight session (`p(d) -> d`) |
| Market adjustment | Simple SPY excess: `X_i,d = ON_i,d - ON_SPY,d` |
| Score direction | Higher is better; higher means a more negative stock overnight excess move |
| Decision timing | After the completed regular daily bar for `d` is available |
| Execution timing | Current engine's next tradable daily-bar `open`, `Open(n(d))` |
| Rebalance cadence | Daily, at each scheduled completed daily bar |
| Selection | At most `top_n = 6` names |
| Position weighting | Equal fixed target weight per selected name: `0.55 / 6 = 0.0916666667`; leave the unused balance in cash when fewer than six qualify |
| Position cap | Current production cap `0.10` per name; the fixed target is below it |
| Direction | Long-only; no short leg and SPY is never traded |
| Universe | Current point-in-time equity scanner and tradability policy, unchanged |
| Equity allocation | Maximum `0.55`, no additional capital |
| Account slots | Current global maximum `6`; candidate is an alternative, not a seventh sleeve |
| Order policy | Current `0.005` rebalance band and `$10` minimum order notional |
| Costs | Current equity model: `2` bps adverse slippage, `2` bps spread, zero commission, effective-dated SEC/TAF/CAT fees; later test includes frozen 1x/2x/3x stress |

The six-name limit is the selection limit, not permission to exceed the
account-level risk overlay. If the overlay, cash constraint, participation
limit, lifecycle state, or completeness gate reduces exposure, those existing
controls remain authoritative. If fewer than six names have a valid score, the
candidate uses only those names at the fixed per-name weight and leaves the
remainder as cash. If no valid cross-section exists, the equity target is zero
until the next valid scheduled decision.

## Universe and holding rule

The candidate uses the current PIT scanner without changing its filters:
minimum price `$5`, trailing median dollar volume `$20m`, minimum data
completeness `0.95`, ATR bounds `0.005`–`0.12`, spread-quality ceiling `50` bps,
supported exchanges NASDAQ/NYSE/ARCA/AMEX/BATS, maximum five names per sector,
dynamic selection, target shortlist `300`, and maximum streamed observation set
`30`. SPY remains a pinned reference and is excluded from trading. Candidate
ranking may select the streamed observation set from the already eligible pool
using the same exact score; it may not broaden the pool or bypass lifecycle
checks.

At each daily completed-bar decision, rebuild the target from the latest valid
`Score_i,d`. Between scheduled decisions, do not rebalance for alpha. The
engine creates net deltas and fills them at the next tradable open, processing
sells before buys. There is no same-session close exit, no synthetic holiday
bar, no stale-score carry into an invalid pair, and no special stop or risk
overlay added for this candidate. Terminal lifecycle behavior remains the
current engine behavior.

## Why this transformation is frozen

The cross-period evidence supports a contrarian direction, while the simplest
common-market control is the contemporaneous SPY overnight component. A rolling
beta would require a long estimation window, add estimation error, and make the
candidate look like a second version of the existing residual-momentum family.
The simple excess transformation is therefore the mechanism-aligned choice. No
sector, Fama–French, machine-learning, or multi-factor residualization is
introduced.

The one-session horizon is the irreducible overnight interval in the research
question. Daily rebalance is fixed because the mechanism concerns the next
regular-session price-formation interval and the engine's next-open order
contract; neither cadence nor horizon will be swept after seeing outcomes.

## Implementation boundary

This task creates no strategy module, config entry, backtest result, or PAPER/LIVE
authorization. A later implementation task may add this identity only after
using the exact normalized data view and the test protocol. Passing the later
economic gates would still authorize only a separate review; it would not
auto-promote the candidate to PAPER or LIVE.
