# Causality audit

## Frozen time notation

Let `d` be a regular NYSE session date and let `p(d)` be the immediately
preceding common SPY/NYSE session date. A symbol is eligible for a signal on
`d` only when its point-in-time lifecycle is valid on both dates and both bars
are present with finite positive prices.

For the one candidate defined in `CANDIDATE_DESIGN.md`:

```text
ON_i,d   = Open_i,d / Close_i,p(d) - 1
ON_SPY,d = Open_SPY,d / Close_SPY,p(d) - 1
X_i,d    = ON_i,d - ON_SPY,d
Score_i,d = -X_i,d
```

`Score_i,d` is higher when the stock's overnight move is more negative relative
to SPY. No close-to-close return, intraday return, future return, beta estimate,
or future volume is part of the score.

## Information → decision → execution

| Event | Information available | Allowed action |
| --- | --- | --- |
| `Close(p(d))` | The prior regular-session close for the stock and SPY. | No candidate decision is made from an incomplete session. |
| `Open(d)` | The overnight component `Close(p(d)) -> Open(d)` becomes observable. | The direct academic same-open trade would submit here, but the current daily strategy path does not. |
| `Close(d)` / completed daily bar `d` | `Open(d)`, `Close(p(d))`, the SPY pair, and the point-in-time universe/lifecycle state are known. | Compute `Score_i,d`, rank the current eligible pool, and create the target. This is the frozen decision timestamp. |
| `Open(n(d))` | `n(d)` is the next tradable session after `d`; its open is not known at decision time. | The current engine fills the pending target at this next bar open, subject to participation, cash, rebalance-band, minimum-notional, slippage, spread, and fee rules. |
| Between decisions | No new alpha target is created. | Hold the current target except for the existing risk overlay, lifecycle handling, and ordinary order completion. |

The strict order condition in the engine is `order.created_at < bar.timestamp`.
Consequently, a target created on completed bar `d` cannot fill at `Open(d)`;
the earliest ordinary fill is `Open(n(d))`. Sells are processed before buys at
that fill timestamp. The candidate therefore freezes a one-session-lagged
overnight reversal implementation. It does not silently call the same-session
close-to-open/open-to-close literature trade reproducible.

## What the current engine can and cannot test

The direct literature exposure is “observe the overnight move at `Open(d)`, buy
the low-overnight names, and capture `Open(d) -> Close(d)`.” The present daily
engine cannot do all three steps: its strategy decision occurs only after the
completed bar, and its normal order model has no same-session close liquidation
for a daily target. Adding a 5-minute/event-time opening decision or a special
close-execution model would be a separate architecture change and is outside
this design task.

The frozen candidate is consequently a distinct, honest hypothesis: the
overnight pressure measured on `d` is sufficiently persistent or recurrent that
the long low-`X_i,d` basket remains useful when entered at the next tradable open.
With the selected daily cadence, a target created after `d` is replaced only at
the next completed daily decision and the order fills at the following open.
Thus the engine's realized holding interval includes the next regular session
and the next opening transition; it is not reported as a pure same-day
open-to-close return. This lag and holding rule are part of the hypothesis, not
an implementation detail to be changed after seeing results.

## Lookahead and calendar controls

The future implementation must enforce all of the following:

* Never use `Open(d)` before the completed-bar decision at `Close(d)`.
* Never use `Open(n(d))`, `High(n(d))`, `Low(n(d))`, or `Volume(n(d))` in the
  score, scanner, sizing, or liquidity-capacity calculation.
* Join `p(d)` by the immediate previous common SPY/NYSE session, not by a
  previous CSV row and not by subtracting one calendar day.
* Use the point-in-time lifecycle interval and provider-asset identity at `d`;
  current active/inactive status is not a historical feature.
* If the stock or SPY pair is missing, stale, duplicated, or outside its
  lifecycle interval, omit that stock from the decision and fail closed when
  the required observation set is incomplete.
* Use the same normalized daily bars for signal and the existing executable
  bar path, with no synthetic weekend/holiday bar.
* Leave the current engine's known-volume cutoff in force; it must use only
  execution history with timestamps at or before order creation.

## Reference and observation-set timing

SPY is a non-traded reference symbol. The scanner's eligible pool remains owned
by the current point-in-time liquidity/tradability policy. The candidate's exact
score may rank that pool for the maximum-30 observation set, but a score cannot
make an ineligible or lifecycle-invalid symbol observable. The future test must
record the daily snapshot effective date and the candidate score's data date so
that a pre-open observation snapshot is never mistaken for a same-open decision.

## Causality verdict

The candidate is causally executable only in its explicitly lagged form:

```text
completed session d and its overnight component
    -> target decision after Close(d)
    -> next-tradable-open fill Open(n(d))
```

Any future implementation that fills at `Open(d)` from `ON_i,d`, or reports the
same-day `Open(d) -> Close(d)` exposure from this daily path, fails the design
and must not produce economic results.
