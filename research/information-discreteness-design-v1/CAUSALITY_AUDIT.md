# Causality and point-in-time audit

The design is causal if the signal at decision bar `t` can be reconstructed
from information available through that completed bar and from PIT eligibility
as of `t`. The intended path is:

```text
completed NYSE session t
  -> PIT scanner and lifetime eligibility as of t
  -> exact aligned closes through t
  -> 12-1 PRET and daily sign counts
  -> positive-winner gate
  -> ascending raw-ID rank over the PIT pool
  -> bounded observation set from that rank (current cap 30)
  -> top six targets
  -> next tradable daily-bar open proxy
```

## Signal window

For a candidate symbol with closes `C[t-273] ... C[t]`, the signal uses only
the 252 returns `r[j] = C[j] / C[j-1] - 1` for `j = t-272 ... t-21`.
The 21 returns from `t-20 ... t` are the skipped recent month. The signal does
not read a close after `t`.

`PRET` is `C[t-21] / C[t-273] - 1`. Daily sign counts use exactly the same
12-1 interval. No future bar, future rank, future inactive flag, future
delisting status, or future corporate-action record is consulted when making
the decision.

## Fail-closed rules

- Fewer than 274 exchange-session-aligned closes: unscoreable.
- A missing required session: unscoreable; do not insert a zero return or
  interpolate a close.
- Duplicate or ambiguous lifetime identity: unscoreable.
- A symbol not eligible in the PIT scanner at `t`: not in the opportunity
  pool, even if it becomes eligible later.
- Reserved roles (`SPY`, `SH`, `PSQ`, `DOG`) cannot become ordinary targets.
- Ties are resolved by canonical symbol ascending, so selection is deterministic.

## Price and corporate-action semantics

The signal uses split-adjusted closes from the canonical bars. Split and stock-
dividend rows are not reapplied. Cash dividends remain account-P&L events and
are not inserted into the published price-path signal because the canonical
ID definition is based on daily price returns, not an added total-return
series.

## Execution causality

The strategy decides from a completed bar and fills at the next tradable bar
open proxy. This is a documented one-session implementation delay relative to
an academic portfolio that may form at a month-end close. It is not a same-close
look-ahead. The monthly cadence and slow 12-1 formation are chosen before any
local outcome, so the delay is a transparent implementation difference rather
than a timing adjustment.

## Structural validation boundary

Only schema, timestamp, lifetime, corporate-action, and synthetic formula
mechanics are in scope for this design audit. A later economic runner must
recompute the signal independently from the frozen contract and must first
prove fresh V3 control parity before any Candidate B outcome is interpreted.
