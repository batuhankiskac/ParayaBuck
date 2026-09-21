# Current Capability Audit

## Scope and starting state

This is a structural, pre-outcome audit. No candidate return, forward return,
bucket return, NAV, P&L, Sharpe, CAGR, drawdown, rank IC, hit rate, or
candidate-versus-V3 outcome correlation was calculated or inspected.

- Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`
- Starting branch: `main`
- Design branch: `research/max-lottery-design-v1`
- Starting worktree: recorded clean by the primary researcher before branch creation
- Local `main`: `86123374e41735d8d163c7fbed5bf5edfc43468d`
- `origin/main`: `86123374e41735d8d163c7fbed5bf5edfc43468d`
- Active branches before this design branch: only `main`
- PAPER strategy: `equity_residual_momentum_v3`
- PAPER equity sleeve: `0.55`
- LIVE: disabled, capital cap `0`

## Production contracts confirmed

The reserved-role contract is active. `SPY` is reference-only and `SH`, `PSQ`,
and `DOG` are approved inverse hedges; they remain observable but cannot be
ordinary alpha targets.

V3 uses completed daily bars, SPY-aligned residual returns, a positive 12-1
residual-momentum gate, an equal-mean percentile consensus of raw 6-1, 9-1,
and 12-1 residual momentum, rejection above the 70th percentile of 63-bar
annualized residual volatility, inverse-residual-volatility sizing, a 10%
per-name cap, and weekly next-tradable-open execution. Production selects up
to eight V3 names; `risk.max_positions=6` is an account-wide, cross-market cap,
so equity can occupy six only when crypto, manual, and reserved positions do
not consume slots.

The corrected historical path is present:

- canonical daily data are SIP and split-adjusted;
- duplicate/conflicting normalized bar keys fail closed;
- cash dividends are portfolio-accounting events;
- split and stock-dividend actions are not reapplied to already adjusted bars;
- explicit terminal events, horizon-censored active histories, and unresolved
  lifetimes remain distinct;
- execution applies adverse slippage, half-spread, commission, effective-dated
  regulatory fees, a 10% participation limit, partial fills, a 0.5% rebalance
  band, and a $10 minimum order;
- fills occur at the next tradable open, not the signal close.

## Scanner and security taxonomy

The scanner has hard gates for a $5 minimum price, $20 million trailing 20-day
median dollar volume, 95% completeness, a 0.5%-12% ATR band, OTC exclusion,
and static leveraged/inverse exclusion. A finite nonnegative spread is required
when supplied. The configured 50 bps spread-quality ceiling is a soft quality
score, not a hard exclusion; missing spreads receive neutral quality.

Discovery is broad Alpaca `US_EQUITY`. Neither live discovery nor historical
data provide a complete point-in-time product taxonomy. ETFs, ETNs, preferreds,
warrants, units, ADRs, and structured products can pass the ordinary scanner.

## Capability relevant to MAX

The repository can calculate split-adjusted close-to-close daily returns and
can group them by calendar month. It also has reusable next-open execution and
all-in cost accounting. It does not yet have a MAX signal implementation.

The generic monthly policy fires on the first completed bar of a new month and
then fills on the following open. A faithful MAX design must observe the final
completed close of month t and first be eligible to fill at the first tradable
open of month t+1. Reusing the generic policy would be one daily bar late.

## Capability conclusion

Close-based MAX is structurally calculable, but the current repository cannot
support an honest canonical test because:

1. the eligible universe is not proven common-stock-only, which is especially
   dangerous for a LOW-MAX rank; and
2. exact month-end-close to next-month-first-open scheduling is absent.

No production code or configuration was changed.
