# Frozen candidate design

Decision: `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`

Candidate name: `equity_information_discreteness_continuous_v1`

Version: `1.0.0`

This is one separate equity alpha alternative. It is not a V3 sleeve, filter,
blend, or production strategy.

## Hypothesis

Among stocks with positive 12-1 momentum, continuation should be stronger when
the gain arrived through many small positive sessions (continuous information)
rather than a few attention-attracting jumps (discrete information).

## Exact signal construction

At completed decision session `t`, require exactly 274 aligned regular-session
split-adjusted closes:

```text
C[t-273], C[t-272], ..., C[t]
```

Define the 252 formation returns and the skipped recent month as:

```text
r[j]       = C[j] / C[j-1] - 1,  for j = t-272, ..., t-21
PRET[t]    = C[t-21] / C[t-273] - 1
```

The 21 returns from `t-20` through `t` are excluded from both `PRET` and the
daily sign counts. Returns are simple close-to-close returns; no log-return or
cash-dividend substitution is used.

Count over the 252 formation returns:

```text
N+       = count(r[j] > 0)
N-       = count(r[j] < 0)
N0       = count(r[j] == 0)
Ntotal   = N+ + N- + N0 = 252
ID[t]    = sign(PRET[t]) * (N- / 252 - N+ / 252)
```

Zero-return sessions stay in the denominator and are not assigned to either
the positive or negative count. This is the canonical published treatment.

## Eligibility and ranking

1. The symbol must be eligible in the unchanged PIT broad equity scanner as of
   `t`, must not be a reserved role, and must satisfy the exact 274-close
   history requirement.
2. Require `PRET[t] > 0`. This is the only momentum threshold; there is no
   percentile or optimized cutoff.
3. Rank eligible positive winners by `ID[t]` ascending. Lower raw ID is more
   continuous for positive winners and is preferred.
4. Break exact ID ties by canonical symbol ascending.
5. Use this same frozen candidate rank to build the current PIT observation
   set: retain the first 30 ordinary eligible names (plus any forced reference
   or managed-role pins required by the existing engine). The stream cap is an
   observation limit, not a new opportunity-universe filter.
6. Select the first six ordinary ranked names, or all ranked names if fewer
   than six are scoreable. Do not shorten the formation window or fill a
   missing name with a fallback symbol.

`PRET` gates the winner set but does not participate again in the final rank.
There is no arbitrary weighted blend with V3 or with another feature.

## Portfolio and execution contract

- Cadence: monthly, at the last completed regular NYSE session of each month.
- Decision: use only the completed decision bar and PIT state at that bar.
- Fill: next tradable daily-bar open proxy, with the existing execution model.
- Target count: `top_n = 6`.
- Weighting: equal weight within the selected names.
- Equity budget: maximum `0.55` of account equity.
- Per-position cap: `0.10` of account equity.
- Exact target formula for `n` selected names: `min(0.55 / n, 0.10)` per
  name, with the residual held as cash. For six names this is `0.55 / 6`;
  for fewer names the cap prevents concentration above 10%.
- Maximum positions: preserve the current six-position account architecture.
- Reserved roles: `SPY` reference-only; `SH`, `PSQ`, and `DOG` hedge-only; none
  may be selected as ordinary Candidate B targets.
- Universe: unchanged broad historical `US_EQUITY` PIT scanner. The
  candidate-specific rank drives the existing maximum streamed observation set
  of 30 after PIT eligibility; no security-master expansion.
- Costs and risk: preserve the incumbent’s slippage, spread, effective-dated
  regulatory fees, rebalance band, minimum order notional, cash, and risk
  policies for the later A/B run.

## Missing-data and identity rules

- Any missing required exchange session, duplicate close, invalid timestamp,
  unresolved identity, or ambiguous lifetime mapping makes the symbol
  unscoreable at that decision date.
- No synthetic zero returns, interpolation, dynamic lookback shortening, or
  future delisting/inactive status is allowed.
- A horizon-censored lifetime can be used only while its PIT observations are
  otherwise valid; explicit future terminal information cannot alter an earlier
  score.

## What is frozen and what is not claimed

Frozen: the formula, direction, 12-1 window, zero-day handling, positive gate,
rank order, tie-break, monthly cadence, six-name selection, equal weighting,
allocation cap, execution, and fail-closed rules above.

Not claimed: any local return, P&L, NAV, IC, hit rate, Sharpe, CAGR, drawdown,
capacity result, or advantage over V3. Those belong only to the later protocol
in `FUTURE_TEST_PROTOCOL.md`.
