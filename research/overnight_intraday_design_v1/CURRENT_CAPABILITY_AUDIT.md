# Current capability audit

## Scope and starting state

This is a pre-outcome design audit. No backtest, signal/forward-return study,
portfolio simulation, P&L calculation, or parameter search was run.

| Item | Verified value |
| --- | --- |
| Repository | `/Users/batuhankiskac/Desktop/ParayaBuck` |
| Working branch | `research/overnight-intraday-design-v1` |
| `main` SHA | `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1` |
| `origin/main` SHA | `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1` |
| Starting HEAD | `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1` |
| Starting HEAD subject | `fix backtest lifecycle and observation identity` |
| Production branch changed | No |

The requested branch already existed at the exact cleaned-main SHA and had no
tracked code changes. It was preserved and used as the design-only branch;
recreating it would have been unnecessary and destructive. The only preexisting
untracked material was the structural data-audit receipt in this directory; it
was recomputed without using economic outcomes.

## Current equity product

The current production selection in `config/settings.yaml` is
`equity_residual_momentum_v3`. It is a `1d` equity strategy using SPY as the
benchmark and a weekly rebalance policy. Its frozen product math is:

* a positive 12-month-minus-one-month market-residual momentum gate;
* raw 6/9/12-month residual-momentum percentile consensus;
* inherited idiosyncratic-volatility rejection at quantile `0.70`;
* `top_n: 8` followed by inverse-volatility sizing capped at `0.10` per name;
* SPY excluded from trading but retained as a reference symbol.

The equity sleeve maximum allocation is `0.55`. The account-level limits are
maximum gross exposure `0.60`, minimum cash `0.40`, maximum position weight
`0.10`, and maximum positions `6`. The separate crypto configuration is out of
scope and remains unchanged. `runtime.mode` is `paper`; `live.enabled` is
`false` and its hard dollar cap is zero.

The current equity universe is dynamic and point-in-time. Its relevant gates are
minimum price `$5`, trailing median dollar volume `$20,000,000`, data
completeness `0.95`, ATR bounds `0.005`–`0.12`, spread-quality ceiling `50`
bps, supported exchanges NASDAQ/NYSE/ARCA/AMEX/BATS, and a maximum of five
names per sector. The scanner has a target shortlist of `300` and a maximum
streamed observation set of `30`, with SPY pinned as a reference. The 30-symbol
stream is an observation-capacity set, not the opportunity universe: the
strategy's exact ranking can replace the scanner fallback ranking when building
that set. Scanner snapshots rebalance daily.

The shared order-existence policy is a `0.005` rebalance band and `$10` minimum
order notional. The current historical equity cost inputs are `2` bps adverse
slippage, `2` bps spread, zero commission, and effective-dated SEC/TAF/CAT
fees. These are constraints for the later experiment, not results of this
audit.

## Backtest and execution path

The relevant implementation is `src/parayabuck/backtest/portfolio_engine.py`.
For each market timestamp the engine:

1. ingests execution bars for the cycle;
2. ingests decision bars through that timestamp;
3. applies corporate actions through that timestamp;
4. fills pending orders;
5. updates prices and risk state;
6. evaluates the strategy on the completed-bar context; and
7. creates net target-to-order deltas.

The engine contract is explicitly “decision on completed bar; next tradable bar
open.” An order is ineligible when its `created_at` is equal to the fill bar
timestamp, so a decision associated with session `d` cannot fill at `Open(d)`.
The next eligible fill is the next tradable bar's `open`; sells are processed
before buys. The participation calculation uses only volume known by order
creation, avoiding use of the future fill bar's completed volume.

For an equity daily bar, the timestamp is a session-date label, while the
bar's semantic availability is after that regular session has closed. The
strategy therefore cannot observe `Close(d)` and then use `Open(d)` in the same
cycle. Holidays and missing symbol observations are not filled with synthetic
prices: the NYSE-aware calendar and timestamp-aligned history determine valid
joins, and the live-parity completeness gate can skip a decision when a streamed
observation is incomplete. Terminal lifecycle handling closes an ending holding
at its actual terminal bar close.

This timing supports a causal lagged overnight hypothesis. It does not reproduce
an academic trade that observes `Close(d-1) -> Open(d)` at the open and submits
at that same open. That distinction is carried into the candidate definition and
is not hidden by naming the provider daily open an auction price.

## Historical data package

The audited package is
`data/historical/equity_pit_symmetric_2016_2026_v5.csv`, with the manifest's
SHA-256
`bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` and
11,686,832 rows for 9,010 symbols. It contains timestamp, symbol, timeframe,
OHLC, volume, and market fields. The local manifest identifies SIP historical
data with split adjustment and regular equity-session coverage. The separate
point-in-time lifetime table contains 9,010 `TIER_A_VERIFIED_PIT` histories,
with 6,858 currently active and 2,152 inactive histories. The corporate-action
inventory contains 141,908 cash dividends, 270 forward splits, 1,207 reverse
splits, and 61 stock dividends.

The raw CSV has provider aliases (`EQUITY`/`equity` and `1Day`/`1d`). The current
CSV loader lowercases market but does not canonicalize the timeframe spelling,
so the future experiment must create a deterministic read-only normalized view:
`EQUITY -> equity`, `1Day -> 1d`, and timezone-aware timestamps converted to
`America/New_York` session dates. It must fail closed on canonical duplicate
conflicts. The structural scan found no duplicate symbol/session-date rows, so
the normalized view is feasible and preserves all 11,686,832 rows. This audit
does not modify the production loader.

The package has 100% finite, positive open and close values, zero duplicate
symbol/session rows, and zero missing prior closes among non-first symbol
observations. That last fact does not make every adjacent observation a
consecutive exchange session: 5,791 observations across 5,648 symbols have a
gap relative to the SPY session calendar. The future signal join must therefore
require the immediately preceding common NYSE/SPY session explicitly.

## Capability conclusion

The current architecture can execute one precisely frozen, long-only overnight
cross-sectional hypothesis if it is defined with a completed-daily-bar decision
and next-tradable-open fill. It cannot honestly claim same-session opening-auction
execution from the present daily strategy path. The provider-open proxy,
normalization rule, common-session join, lifecycle join, and cost stress are
mandatory conditions of the later economic test. No production strategy,
configuration, risk limit, or PAPER/LIVE boundary was changed here.
