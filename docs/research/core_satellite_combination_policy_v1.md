# Core-satellite combination policy v1

Status: durable research methodology

This policy defines a candidate-independent experiment for asking whether a
previously standalone-valid, long-only equity alpha adds value beside canonical
`equity_residual_momentum_v3`. It may be reused for future complementary-alpha
tests unless it is superseded and the replacement is frozen before any outcomes
from the new campaign are inspected.

It is not a production configuration, strategy registration, PAPER change, or
LIVE authorization.

## Three-arm experiment

All arms must use the same immutable input bytes, chronology, fills, costs,
corporate actions, lifecycle rules, and account constraints.

- Control A is the canonical six-slot V3 portfolio.
- Control B is a five-slot V3 core plus one reserved satellite slot held in
  cash. It computes the candidate reservation but never buys it.
- Treatment C is the identical five-slot V3 core and reservation sequence plus
  the frozen candidate in the satellite slot.

The equity budget is split by slot: 5/6 for the V3 core and 1/6 for the
satellite. With the current 0.55 equity sleeve, those budgets are
0.4583333333333333 and 0.0916666666666667. A future protocol may use different
account constants only if they are frozen before outcomes.

Control B isolates the cost of reserving a slot and capital for the candidate.
Therefore a treatment result must be compared with both canonical Control A and
matched-cash Control B.

## Reservation and ownership

The frozen candidate definition determines the reservation cadence and ranking.
The reserved symbol is excluded from V3 source positions in both B and C, so the
B/C core targets and reservation path are identical before C's satellite target
is appended. A symbol cannot be simultaneously core- and satellite-owned.

Reservation changes occur only on the candidate's frozen decision schedule.
When candidate and V3 decisions share a timestamp, the reservation update comes
first. Lifecycle and risk rules may reduce or close the satellite, but they must
not trigger an outcome-driven substitute or unscheduled rerank.

There is no source-capital transfer. Unused core budget, unused satellite
budget, failed-order cash, and a vacant slot remain cash. No source may borrow
another source's unexpressed allocation.

## Shared-ledger requirement

Each arm has one cash balance, holdings book, order queue, cost ledger, and NAV
curve. Core and satellite labels are attribution metadata on targets, orders,
fills, holdings, realized P&L, and costs; they are not separately funded NAV
curves blended after the fact.

The candidate and V3 identities must remain frozen. Decisions use completed
bars and orders fill on the next tradable bar. Validation is a continuous
out-of-sample segment, not a state reset. All arms use the same effective-dated
all-in cost model and the same predeclared cost multipliers.

## Pre-outcome protocol

Before any combination outcomes are inspected, record and hash:

- the candidate's accepted standalone evidence and immutable definition;
- the canonical V3/control identity;
- account budgets, source caps, reservation and event order;
- dataset, lifecycle, corporate-action, execution, and cost contracts;
- development and validation boundaries;
- all mandatory acceptance gates and tolerances;
- the independent verification procedure.

Diagnostics may explain a result but may not become new acceptance gates after
outcomes are known.

## Minimum validity checks

A combination run is valid only when an independent verifier confirms:

- exact candidate and V3 identities;
- identical B/C core targets before the satellite is appended;
- complete source attribution and no core/satellite overlap;
- one shared ledger per arm and no post-hoc NAV blending;
- no lookahead and complete decision coverage;
- lifecycle, corporate-action, fill, and cost correctness;
- unchanged production strategy, configuration, PAPER, crypto, and LIVE
  boundaries; and
- no broker orders or other external production effects.

Acceptance gates should cover, at minimum, candidate incremental value versus
B, portfolio contribution versus A, validation contribution, cost robustness,
satellite deployability, turnover economics, concentration, and architecture
safety. Every mandatory gate is conjunctive unless the frozen protocol states
otherwise.

## Decision semantics

- An invalid experiment is `COMBINATION_INCONCLUSIVE`.
- A valid experiment with any failed mandatory gate is `COMBINATION_REJECT`.
- A valid experiment with every mandatory gate passing may be accepted only for
  the next explicitly authorized research or forward-PAPER stage.

A valid combination rejection does not erase positive standalone evidence. It
means only that the alpha did not earn a slot under the frozen architecture.
Use `ALPHA_VALID_PORTFOLIO_REJECTED` for that case. Conversely, a weak or
negative standalone candidate should be `ALPHA_REJECTED` without running this
combination protocol.

No decision under this policy automatically changes production, PAPER, or LIVE.
