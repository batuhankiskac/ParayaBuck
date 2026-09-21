# MAX / Lottery Preference Pre-Outcome Design Report

## Design decision

`DESIGN_BLOCKED_DATA_OR_EXECUTION`

Architecture D is selected for the current repository state. No candidate is
frozen and no economic test is authorized. The required universe verdict is
`UNIVERSE_LIMITATION_INVALIDATES_MAX_TEST`.

## Starting main SHA

Local `main` and `origin/main` were both
`86123374e41735d8d163c7fbed5bf5edfc43468d`; the primary researcher recorded
the starting worktree as clean before creating the design branch.
The design branch is `research/max-lottery-design-v1`.

PAPER uses `equity_residual_momentum_v3` with a 0.55 equity sleeve. LIVE is
disabled with zero capital. Reserved reference/hedge roles and corrected
loader, lifecycle, corporate-action, next-open, and all-in-cost contracts are
active.

## Literature evidence

The seminal U.S. result is credible historical evidence for a negative
high-MAX subsequent-return relation. It survives several original controls and
some price/size restrictions. European and Australian extensions provide
partial support. However, the spread is concentrated substantially in poor
high-MAX returns; the low-MAX long leg is not established as a standalone
six-name alpha. Later investability-oriented U.S. replication and developed-
market persistence evidence are materially weaker, and no decisive MAX-specific
net-cost validation exists.

Primary anchors are [Bali, Cakici & Whitelaw](https://doi.org/10.1016/j.jfineco.2010.08.014),
[Fong & Toh](https://doi.org/10.1016/j.jbankfin.2014.05.006),
[Hou, Xue & Zhang](https://doi.org/10.1093/rfs/hhy131), and
[Egginton & Hur](https://doi.org/10.1016/j.jempfin.2018.03.001).

## Exact MAX definition

The seminal measure is MAX1: the single maximum daily simple stock return in
the immediately preceding completed calendar month, with no skip, used to
predict the following month. Higher MAX is worse. CRSP daily-return field
semantics are not identified by name in the paper, so a repository price-only
close-to-close version would be a disclosed proxy rather than an exact
field-level replication.

MAX1 is the only defensible starting variant if a future repository state is
redesigned: it is seminal, simple, auditable, and less IVOL-like than MAX5. It
is not frozen as a candidate in this blocked decision.

## MAX versus IVOL

MAX and IVOL are highly overlapping, with published correlation ranging from
about 0.75 to about 0.90. MAX often survives IVOL controls, but mutual-control
results are unstable and IVOL can retain content in overpriced subsets. The
evidence does not establish MAX as a uniquely identified lottery-preference
primitive.

## Distinctness from V3

V3 rejects the highest 30% of 63-bar residual volatility and inverse-volatility
weights survivors. It therefore consumes the low-idiosyncratic-risk direction
both in eligibility and sizing. MAX1 is nonlinear and shorter horizon, but a
LOW-MAX strategy would still load heavily on the same defensive axis. Applying
MAX within V3 would skip the required standalone-alpha question and confound
the result with V3's IVOL design.

## Mechanism interpretation

Lottery-seeking demand is plausible and supported by theory and investor-
clientele evidence. It is not uniquely identified. Reversal, overreaction,
microstructure, skewness, attention, short-sale constraints, mispricing state,
and aggregate-volatility insurance can generate similar patterns. Conditional
MAX evidence often requires inputs unavailable here or belonging to closed
research families.

## Large/liquid stock evidence

The original spread survives a $5 floor, NYSE-only tests, and removal of the
smallest NYSE stocks; an Australian largest-500 result is supportive. Against
that, robust U.S. NYSE-breakpoint/value-weighted replication and later U.S.
subperiod evidence are weak. The current scanner's $5 and $20 million gates do
not by themselves validate a six-name LOW-MAX portfolio.

## Universe limitation

`UNIVERSE_LIMITATION_INVALIDATES_MAX_TEST`

The broad `US_EQUITY` history is not proven common-stock-only. Bond/defensive
ETFs, ETNs and preferreds can rank mechanically low on MAX, while warrants,
units and structured products can occupy extreme tails. Because LOW-MAX seeks
quiet products, this risk is directional and can turn the signal into a product
classifier. Current price/liquidity/ATR/static leveraged-product gates do not
solve the missing point-in-time taxonomy.

## Data feasibility

The canonical file contains 11,686,832 structurally valid bars for 9,010
entities over 2,659 sessions from 2016-01-04 through 2026-07-31, with no
normalized duplicate keys, missing CSV fields, weekend rows, nonfinite OHLCV,
or impossible OHLC envelopes. Bars are SIP and split-adjusted. Known AAPL,
TSLA, NVDA and GE split/reverse-split close boundaries do not show factor jumps.

Lifetimes contain 6,858 active/horizon-censored histories, 2,151 explicit
possible-delisting terminals, and one unresolved EOSEW record. Corporate
actions contain 141,908 cash dividends, 61 stock dividends, 270 forward splits
and 1,207 reverse splits; split rows must not be reapplied. SPY lacks the final
2026-07-31 bar, so benchmark-dependent work must end at 2026-07-30 or fail
closed.

Data are adequate to calculate a close-based statistic, but not to establish
the required common-stock construct.

## Causality

A faithful experiment must use only completed month-t sessions, decide after
the final exchange close, and fill no earlier than the first tradable open of
month t+1. The current generic monthly policy instead fires after the first
completed bar of month t+1 and fills at the following open, one daily bar late.
The next-open engine is reusable, but the missing decision boundary requires
synthetic implementation proof before economics.

## Exact candidate

Not applicable. The decision is BLOCKED; creating `CANDIDATE_DESIGN.md` would
falsely freeze an experiment that cannot be interpreted honestly.

## Future standalone test

Not designed. `FUTURE_TEST_PROTOCOL.md` is intentionally absent because the
request permits it only when READY. No backtest, parameter sweep, or local
outcome inspection occurred.

## Frozen standalone gates

None. Freezing economic gates for an inadmissible candidate would create a
misleading protocol. Any future unblocked design must freeze its own exact
standalone gates before economics and must not require outperforming V3.

## Future V3 comparison semantics

The durable governance rule remains: V3 is contextual/displacement evidence,
not a mandatory standalone-alpha gate. A later candidate that passes all
standalone gates would become `ALPHA_VALID_COMBINATION_UNTESTED` even if it is
weaker than V3 standalone. No candidate has attained that status here.

## Future combination eligibility rule

Only a candidate that passes a separately frozen standalone protocol may enter
the canonical 5+1 core-satellite complementarity test. This task does not reach
Stage 1 and therefore cannot authorize Stage 2.

## Red-team findings

The decisive findings are common-stock taxonomy failure, one-session-late
monthly scheduling, weak evidence for the long-only low leg, top-six
concentration, strong IVOL/V3 overlap, mixed large/liquid persistence, mechanism
ambiguity, single-observation noise, and absent MAX-specific net-cost proof.

## Confirmation of no local economic peek

No candidate future returns, NAV, P&L, Sharpe, CAGR, drawdown, rank IC, bucket
returns, hit rate, candidate-versus-V3 outcome correlation, or local parameter
sweep was calculated or inspected. Archived hidden candidate outcomes were not
opened. An independent receipt records the verification.

## Files

- `CURRENT_CAPABILITY_AUDIT.md`
- `LITERATURE_REVIEW.md`
- `V3_IVOL_DISTINCTNESS_AUDIT.md`
- `DATA_FEASIBILITY.json`
- `UNIVERSE_LIMITATION_AUDIT.md`
- `CAUSALITY_AUDIT.md`
- `DESIGN_RED_TEAM.md`
- `NO_ECONOMIC_PEEK_RECEIPT.json`
- `DESIGN_DECISION.json`
- `FINAL_DESIGN_REPORT.md`

`CANDIDATE_DESIGN.md` and `FUTURE_TEST_PROTOCOL.md` are intentionally absent.

## Branch SHA

The commit containing this report is the authoritative design snapshot. Its
exact SHA is recorded externally in the final delivery because a commit cannot
contain its own content-addressed SHA.
