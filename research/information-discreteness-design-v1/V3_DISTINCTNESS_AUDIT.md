# Distinctness audit against `equity_residual_momentum_v3`

This is a design comparison, not a local performance comparison. No
candidate outcomes were calculated.

## Control capabilities already in V3

The canonical incumbent uses a positive 12-1 **SPY-residual** momentum gate,
raw 6/9/12 residual-momentum percentile-rank consensus, an idiosyncratic
volatility rejection, inverse residual-volatility sizing, and weekly cadence.
It therefore already measures the magnitude and multi-horizon consistency of a
stock’s residual momentum.

## Architecture choices

| Choice | Pre-outcome assessment |
|---|---|
| A. Separate raw-price momentum alternative | **Selected.** Keeps the published ID definition intact, gives the path-quality mechanism clean attribution, and competes for the same scarce six slots. |
| B. Add ID inside V3 | Not selected. It would test an interaction between raw path shape, residualization, consensus, IVOL, and inverse-vol sizing rather than the standalone hypothesis. |
| C. Filter V3-qualified names by ID | Not selected. It would make ID conditional on V3’s already complex selection and make a failure ambiguous between no incremental information and an interaction failure. |
| D. Other blend or composite score | Not selected. No literature basis exists for an arbitrary weight or coefficient, and optimization is out of scope. |

## Why the selected alternative is distinct

Candidate B uses:

1. raw split-adjusted daily stock returns rather than SPY-residual returns;
2. a single 12-1 positive-return gate rather than residual 6/9/12 consensus;
3. the signed daily positive/negative/zero path count rather than residual
   volatility or IVOL rejection;
4. an ascending low-ID continuity sort rather than percentile consensus;
5. equal weights rather than inverse residual-volatility weights; and
6. monthly rather than weekly rebalance.

The two strategies still share the canonical PIT scanner, data, reserved-role
policy, six-position architecture, 0.55 equity budget, cost engine, and
next-open execution. That is intentional: the later A/B test isolates the
alpha construction while holding account mechanics constant.

## Raw versus residual returns

The selected candidate computes ID from raw stock daily close-to-close returns.
This is faithful to the primary Frog-in-the-Pan definition. The paper’s
“residual ID” robustness exercise residualizes the **ID characteristic across
stocks** against controls; it is not the same operation as V3’s stock-return
residualization against SPY. Substituting V3 residual returns would therefore
be a new research hypothesis and would entangle the mechanism with the
incumbent’s market model. No such substitution is made.

## Overlap acknowledged, not hidden

The candidate contains a positive momentum gate, so it is not orthogonal to
momentum. Later literature also finds a common component between continuity-
conditioned momentum and conventional momentum. The right test is therefore a
fresh, parity-controlled displacement experiment—not a claim that ID is an
independent factor. The future gates require meaningful risk-adjusted
improvement over a fresh V3 control; a positive standalone return alone is not
enough.

## Product-universe limitation

The broad historical `US_EQUITY` universe is not a proven common-stock-only
taxonomy. A price path and ID can technically be calculated for an ETP, but
the literature’s behavioral interpretation is mainly common-stock oriented.
This is acceptable for one A/B test because both arms use the same canonical
PIT pool and reserved roles; the result must not be generalized as a
common-stock-only effect. It is a stated interpretation limitation, not a
silent universe expansion.

## Decision

Select architecture A only: a separate, raw-price, positive-winner
continuity-sorted alternative named
`equity_information_discreteness_continuous_v1`. No V3 blending, no V3 code
change, and no production registration are part of this design.
