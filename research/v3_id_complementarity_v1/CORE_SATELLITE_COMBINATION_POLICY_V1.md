# Core + Satellite Combination Policy v1

Status: `FROZEN_BEFORE_B_C_ECONOMICS`

This is a research-only, outcome-independent policy for combining a previously
standalone-valid long-only equity alpha with the canonical production V3 core.
It is not a production configuration, strategy registration, PAPER change, or
LIVE authorization. It applies to this campaign and is the default architecture
for future previously validated complementary alphas unless superseded before a
future campaign's economic outputs are inspected.

## Experiment question

The experiment asks whether one frozen satellite slot adds incremental value
beside canonical `equity_residual_momentum_v3`. It does not ask whether the
satellite replaces V3's six equity slots. The three arms are run as separate
actual account ledgers over the same bytes, chronology, fills, costs, corporate
actions, and lifecycle rules.

* **Control A:** canonical V3, equity sleeve `0.55`, maximum six positions.
* **Control B:** five-slot V3 core plus the candidate-derived reservation slot
  held in cash.
* **Treatment C:** the identical five-slot V3 core and reservation sequence as B
  plus the frozen information-discreteness (ID) satellite.

No NAV curves are blended after the fact. Source labels are metadata on targets,
orders, fills, holdings, realized P&L, and diagnostics.

## Frozen 5+1 architecture

The account architecture is selected before combination outcomes:

| Boundary | Frozen value |
|---|---:|
| Equity sleeve | `0.55` |
| Global account maximum positions | `6` |
| Per-name maximum | `0.10` |
| Nominal slot budget | `0.55 / 6 = 0.0916666666666667` |
| V3 core budget in B/C | `0.4583333333333333` |
| V3 core source-position cap in B/C | `5` |
| Satellite budget | `0.0916666666666667` |
| Satellite source slots | `1` |

The V3 core is still the canonical positive-12-1 gate, raw 6/9/12 consensus,
IVOL filter, source rank, inverse-volatility sizing, and weekly decision cadence.
The research wrapper only applies the reduced core budget and the five-position
source cap. It never equal-weights or retunes V3, and unexpressed core budget
remains cash.

The satellite uses the exact frozen ID alpha: split-adjusted simple returns,
252-return formation, 21-return skip, exact PRET, exact ID, zero handling,
positive PRET gate, ascending ID rank, monthly candidate cadence, and
fail-closed session/lifecycle/identity rules. Its definition is separate from
the shared allocator. Among the frozen candidate-qualified top-six set, the
highest-ranked symbol is reserved; no lower-ranked performance substitution is
allowed. A valid reservation receives exactly `0.0916666666666667`, without
risk-sizing or volatility-sizing, subject to the existing global `0.10` cap and
shared execution/risk rules.

## Reservation and event order

The current reservation symbol is excluded from V3 source positions in both B
and C. Therefore their V3 core targets, reservations, and all core decisions
are identical before C's satellite target is appended. B computes the candidate
ranking only to establish the matched reservation and does not buy it.

The reservation updates only on the frozen monthly candidate decision. Between
candidate decisions it is held; there is no daily or weekly candidate rerank.
Lifecycle and risk rules may reduce or exit it. On a reservation change, both B
and C adopt the new reservation first, any exposure to the newly reserved symbol
is reduced/closed under the research allocator, and no unscheduled replacement
V3 name is inserted. The next canonical V3 weekly decision performs ordinary
core replacement. If monthly and weekly events share a timestamp, reservation
update precedes the V3 core decision.

There is no capital transfer: unused core allocation, unused satellite
allocation, failed satellite order capital, and vacant slot budget all remain
cash. No source symbol can be simultaneously core- and satellite-owned.

## Shared account and execution contract

Each arm has one cash balance, holdings book, order queue, cost ledger, and NAV
curve. Decisions use completed bars and orders fill on the next tradable bar.
The canonical corrected PIT dataset, entity lifetimes, corporate actions,
normalization, dividend de-duplication, and endpoint are identical in A/B/C.
Warmup is 2016; development is 2017-01-03 through 2021-12-31; validation is
2022-01-03 through the canonical endpoint; the validation boundary is not a
state reset. V3 remains weekly and ID remains monthly.

Every arm is run at all-in canonical equity cost multipliers `1x`, `2x`, and
`3x`, scaling the current effective-dated slippage, spread, SEC, TAF, and CAT
model with no sleeve-specific rates.

## Frozen mandatory gates

All gates are evaluated after an independent verifier and cannot be changed
after economic outputs are inspected.

### G1 — experiment validity

Pass only when candidate and V3 identities are exact; B/C core target paths are
identical before the satellite; attribution is complete; no overlap or
lookahead exists; lifecycles, corporate actions, costs, and decision coverage
verify; and the independent verifier passes.

### G2 — ID incremental value versus B

At 1x, C must beat B on both net total return and CAGR in full and validation
periods. Independently attributed ID cumulative net P&L must be positive in
both periods.

### G3 — portfolio contribution versus A

At full-period 1x, at least two of CAGR, Sharpe, Sortino, and Calmar must
improve versus A. A non-improving metric may not be below A by more than:
CAGR `0.50` percentage points; Sharpe `0.03`; Sortino `0.03`; Calmar `0.03`.
Treatment C max drawdown may not be more than `1.50` percentage points worse.

### G4 — validation contribution

At validation 1x, C net return and CAGR must be positive, at least one of CAGR,
Sharpe, Sortino, and Calmar must exceed A, max drawdown may not be worse than A
by more than `1.50` percentage points, and C must beat B on net return and CAGR.

### G5 — cost robustness

At 2x, C full net return must exceed B and ID full net P&L must be positive. At
3x, both must be non-negative (`C >= B`, ID P&L `>= 0`). C Sharpe may not be more
than `0.05` below A at both 2x and 3x; if it is below at both stresses, fail.

### G6 — satellite deployability

When a valid frozen top-six candidate exists, a reservation must be established
on at least `95%` of monthly decisions. C must obtain a nonzero satellite target
on at least `90%` of those decisions unless a documented account/risk/execution
constraint blocks it. Unexplained zero-satellite decisions are zero; no position
exceeds `0.10`; account positions stay at six or fewer; and no structural
capacity shortfall is introduced.

### G7 — turnover economics

At 1x, C annualized one-way target turnover may not exceed A by more than `2.0x`.
Satellite transaction costs must be lower than satellite gross positive trading
contribution in full and validation, and at 2x satellite costs must not consume
the full-period gross contribution.

### G8 — concentration

At full-period 1x, no satellite symbol may account for more than `35%` of
cumulative absolute satellite net P&L; at least 12 distinct satellite symbols
must trade unless frozen eligibility structurally yields fewer; and the
leave-one-largest-absolute-contributor attribution must leave satellite net P&L
non-negative. This is attribution-only; no exclusion rerun is permitted.

### G9 — architecture and production safety

Control A remains canonical; B/C use at most six account positions; there is no
source-capital transfer; each arm is a shared ledger; the ID signal is unchanged;
main, PAPER V3, configuration, risk, crypto, and LIVE boundaries are unchanged;
and no broker orders are placed.

All G1–G9 are mandatory. A genuine failed combination is `COMBINATION_REJECT`;
only implementation/experimental invalidity is `COMBINATION_INCONCLUSIVE`.

## Diagnostics (not gates)

Report standalone V3/ID return correlations, monthly correlations, reproducible
selected-name overlap, reservation overlap with unconstrained V3 names, drawdown
overlap, and descriptive satellite contribution by V3 market state. Also report
intended/realized core and satellite exposure, cash and unused-budget reasons,
source P&L/cost attribution, turnover, and concentration. Diagnostics may not be
promoted into new gates after the fact.

## Decision vocabulary

An independently verified valid run returns exactly one of:

* `COMBINATION_ACCEPT_FOR_FORWARD_PAPER_EVALUATION` — every G1–G9 passes;
* `COMBINATION_REJECT` — the run is valid and at least one mandatory gate fails;
* `COMBINATION_INCONCLUSIVE` — the run is invalid.

Acceptance does not modify PAPER or authorize LIVE. A valid rejection preserves
the prior standalone decision and means only that the alpha did not earn a slot
under this frozen architecture.
