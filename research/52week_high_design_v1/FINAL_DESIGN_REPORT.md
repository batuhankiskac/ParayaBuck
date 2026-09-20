# Final 52-week-high design report

## Design decision

`DESIGN_BLOCKED_DATA_OR_EXECUTION`

One reference-price hypothesis is sufficiently defined and conditionally
supported, but the current generic data and execution paths cannot run the
frozen experiment honestly. No economic run is authorized until every G1
prerequisite in `FUTURE_TEST_PROTOCOL.md` passes.

## Starting main SHA

Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`
Starting branch: `main`
Starting status: clean
Fetched local `main`: `db1a6e9e4af4a5000dca0a54a971ce99577c676c`
Fetched `origin/main`: `db1a6e9e4af4a5000dca0a54a971ce99577c676c`

Design branch: `research/52week-high-design-v1`.

## Canonical V3 control

The current control remains `equity_residual_momentum_v3` exactly as documented
in `docs/research/canonical_v3_baseline.md`: 1d, weekly, positive 12-1 residual
gate, raw 6/9/12 residual-momentum rank consensus, inverse-vol sizing, `0.55`
maximum equity allocation, `0.10` single-position cap, and the repaired
offline-drawdown semantics. SPY is reference-only; SH, PSQ, and DOG are
hedge-only. PAPER remains active and LIVE disabled. This task neither redefines
nor promotes the control.

## Literature findings

| Finding | Evidence assessment |
|---|---|
| A rolling-maximum ratio differs algebraically from cumulative return | Strong: the maximum embeds the price path, not just endpoints |
| Incremental content after standard momentum controls | Strong in George-Hwang's original U.S. study; supported in some international replications |
| Long-only winner-leg support | Positive but materially weaker than the original long-short spread; the loser/short leg is important |
| Large/liquid U.S. transferability | Mixed to negative in large-firm and post-publication U.S. studies |
| Transaction-cost robustness | Weak: international and Australian studies find significance often disappears after costs |
| Anchoring/reference-point mechanism | Plausible and supported by trading/expectations/M&A evidence, but not causally established by portfolio returns |
| Later international evidence | Mixed; recent high-quality work reports limited standalone anchoring-proxy support |

The evidence warrants defining one controlled experiment, not assuming that
the effect works or that it is production-relevant.

## Distinctness from conventional momentum

The candidate uses current price relative to a path-dependent annual maximum;
V3 uses residual 6/9/12 return consensus with a positive 12-1 gate. The formulas
are not monotonic transformations of each other. Nevertheless, empirical
correlation with recent momentum is known to be high, and later literature
does not consistently find incremental power. The candidate is therefore
named reference-price momentum and is not claimed to be independent,
orthogonal, or complementary before testing.

## Behavioral mechanism

Documented evidence shows that prior highs are salient and can affect trading,
expectations, and negotiated prices. The candidate interpretation is
under-adjustment to information near an anchor. Generic information
underreaction, risk compensation, and ordinary momentum overlap remain viable
explanations. A return experiment cannot identify psychology by itself.

## Highest-high versus highest-close decision

Highest daily **close** is frozen. It is the closest deterministic translation
of the literature's observed price/closing-price implementations and avoids
one-print intraday extrema. The local structural audit found no mechanical
split discontinuity in opens or closes but found three reverse-split-adjacent
extreme intraday highs, reinforcing the pre-outcome robustness choice. Highest
intraday high is not an alternative in the frozen design.

## Lookback decision

Use exactly 252 valid completed trading sessions, including the completed
decision session `t`. This deterministic trading-calendar mapping implements
one year without a local horizon sweep. Fewer than 252 sessions is
unscoreable; IPO/new-listing histories are never shortened dynamically.

## Data feasibility

The canonical package hashes match the baseline. It contains 11,686,832 bars,
9,010 identities, and 2,659 normalized sessions. Missing/nonpositive highs and
closes and duplicate symbol-session rows are all zero. SPY is present through
the common 2026-07-30 endpoint. Of 9,010 symbols, 7,572 eventually reach 252
valid sessions and 1,438 do not.

Lifetimes cover every bar symbol with zero out-of-boundary bars. However,
complete ticker-change coverage is not proven and one inactive entity lacks a
terminal boundary. Ambiguous entities must fail closed.

The blocking issue is operational compatibility: 7,771,468 bar rows use
`1Day`, which the generic loader does not normalize before its session check,
and the generic action loader does not accept the canonical action schema. The
same canonical normalization, lifetime, and dividend-deduplication path used
by the control must be frozen and deterministically tested before economics.

## Split/corporate-action safety

The bars are split-adjusted. Across material two-sided split events, the audit
found zero mechanical open or close discontinuities. Splits and stock dividends
must not be reapplied. Cash dividends remain separate account cashflows and
never enter the price-high numerator or denominator. The action ledger contains
157 duplicate cash-dividend economic keys, so the canonical apply-once contract
must be preserved.

## Universe / security-master limitation

Use the production PIT scanner unchanged. Do not add market-cap, sector, or
liquidity rules and do not solve the security master here. The broad historical
`US_EQUITY` pool is not proven common-stock-only; both arms share that canonical
limitation. SPY/SH/PSQ/DOG remain nonordinary roles. Results may not be described
as a pure common-stock test.

## Causality

Information through the final completed session of the month feeds one shared
score; the broad eligible pool is ranked and truncated; the target is decided
after the close; the earliest fill is the next tradable open. No same-close or
future-bar field is permitted.

The current engine's order sequencing is causal, but its generic monthly policy
decides after the first completed session of the new month, and its broad-pool
observation rank stops at `t-1`. Both differ materially from the frozen
month-end-through-`t` convention and require research-only, fixture-tested
support.

## Exact candidate specification

Exactly one candidate is frozen: `equity_52week_high_close_v1`.

```text
score(i,t) = split_adjusted_close(i,t)
             / max(split_adjusted_close(i,s), s = t-251,...,t)
```

Higher score ranks first. The decision bar is included. Require 252 valid,
completed daily closes. Rank the unchanged broad PIT pool, exclude reserved
roles from ordinary targets, select six, and tie-break by canonical symbol.
Missing, stale, nonpositive, nonfinite, or lifecycle-ambiguous input fails
closed without imputation.

## Portfolio construction

The candidate is a long-only alternative to V3, not an additional sleeve. Use
six equal-ranked slots. Each of `n` available targets receives
`min(0.55 / n, 0.10)` and unused capacity stays cash. Allocation is at most
`0.55`; position cap is `0.10`; shared account slots, gross, cash, lifecycle,
risk, and execution rules remain unchanged. Equal weighting isolates the
reference-price ranking and does not import V3's residual-volatility sizing.

## Rebalance cadence

Decide monthly after the last completed trading session and execute no earlier
than the next tradable open. Hold between scheduled decisions except for
canonical lifecycle, execution, reconciliation, and risk-reducing actions. No
stop, secondary exit, or weekly/daily cadence variant is allowed.

## Future economic test

Run only after G1 passes. Fresh Control A and Candidate B use the canonical
2016 warmup, 2017-01-03 to 2021-12-31 development, and 2022-01-03 to 2026-07-30
validation windows. Run 1x/2x/3x all-in costs with effective-dated fees scaled
as well as spread/slippage. Do not reuse old control numbers. Do not add a
combined portfolio arm. No result authorizes automatic PAPER/LIVE promotion.

## Pre-frozen gates

All nine gates are conjunctive and fully specified in
`FUTURE_TEST_PROTOCOL.md`: data/implementation validity; positive standalone
economics; full-period risk-adjusted displacement; independent validation;
drawdown/Calmar discipline; 1x/2x/3x cost robustness; bounded turnover and
fills; concentration robustness; and architecture/capital parity. The key
displacement hurdle requires at least two of Sharpe, Sortino, and Calmar to
improve by 0.10 at full-period 1x, with bounded degradation and no more than a
1.00 percentage-point CAGR tradeoff.

## Red-team findings

Material objections remain: momentum overlap, weaker long-only evidence,
mixed post-publication/large-stock results, cost fragility, broad-universe
taxonomy, equal-weight risk differences, and current execution gaps. None
justifies tuning another form. Together they justify one frozen hypothesis and
a blocked status until the exact data/execution prerequisites pass.

## Confirmation of no local economic peek

No candidate return, future return, P&L, NAV, CAGR, Sharpe, Sortino, Calmar,
drawdown, profit factor, hit rate, IC, bucket spread, outcome correlation, or
parameter-performance grid was computed or inspected. No sanity backtest ran.
No hidden/archive candidate result was opened. Only literature, code/docs, and
structural counts were used.

## Files created

- `CURRENT_CAPABILITY_AUDIT.md`
- `LITERATURE_REVIEW.md`
- `DATA_FEASIBILITY.json`
- `CAUSALITY_AUDIT.md`
- `CANDIDATE_DESIGN.md`
- `FUTURE_TEST_PROTOCOL.md`
- `DESIGN_RED_TEAM.md`
- `DESIGN_DECISION.json`
- `FINAL_DESIGN_REPORT.md`

## Branch SHA

The immutable final branch SHA is recorded by the external handoff after the
design commit (`git rev-parse HEAD`); a commit cannot contain its own SHA. No
production path is included in that commit.
