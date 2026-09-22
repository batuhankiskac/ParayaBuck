# Search methodology — public-data alpha discovery v3 — 2026-09-22

PRE-OUTCOME discovery. No local forward returns, CAR, IC, quantile returns, Sharpe, CAGR,
P&L, validation returns, V3-outcome correlations, or optimized windows were calculated.
Structural counts (filing/record coverage, mapping rates) are permitted only to answer
"can the signal be constructed" — never connected to future performance.

## Objective

Find NEW equity alpha families with enough alpha density to justify one of very few slots
(account max 6 positions; equity sleeve 0.55; max name weight 0.10), relying ONLY on
accessible data per `docs/research/public_data_research_policy_v1.md`. Prefer signals that
naturally identify a small number of unusually informative issuers/events. No generic
diversified anomaly.

## Incumbent architecture (verified from clean main `82e534f`)

- Equity: `equity_residual_momentum_v3` (12-1 residual gate, 6/9/12 rank consensus, ivol
  filter, weekly rebalance, next-tradable-open execution, daily signal architecture).
- Crypto: `crypto_low_variance_hysteresis_v1` (sleeve 0.05).
- Account: max 6 positions, equity sleeve 0.55, max single name 0.10, ~$20M
  median-dollar-volume floor.
- Historical `US_EQUITY` NOT proven PIT common-stock-only — contamination by
  ETFs/ETNs/preferreds/warrants/rights/units/structured products possible.
- Satellite must: occupy one slot efficiently, differ informationally from V3, survive
  long-only, work in liquid stocks, retain edge after public observation.

## Search organization

Three parallel evidence scouts with disjoint ownership (no shared files, no recursion):

1. `SECFilingEvents` — SEC filing events: unusual insider-ownership events (distinct from
   rejected purchase-size), 8-K item categories, financing/debt/material agreements,
   director/officer changes (only with drift evidence), filing delays/amendments/restatements,
   accounting changes, going-concern resolution (only with long-leg evidence).
2. `FINRAShortInterest` — FINRA short-interest source feasibility (depth, publication vs
   settlement lag, corrections, coverage, bulk access, identifiers, denominators) + modern
   long-leg/liquid/concentration evidence. Prior close lead re-examined; no promotion merely
   for being public.
3. `CorpActionMechText` — corporate actions with post-announcement continuation (tender,
   exchange, debt retirement, unusual distributions, recapitalizations, separations, listing
   transfers); exchange/public mechanical events with exact free timing (uplisting,
   delisting-warning resolution, status changes); SEC textual structure (deterministic/frozen
   measures only; text models only with realistic historical provenance); other free official
   sources with causal timestamps.

Parent integrated: long list (12–20 families), evidence review, concentration review, source
matrix, shortlist (≤5), scorecards, decision. Closed families never recycled without
materially NEW mechanism/evidence (stated explicitly).

## Screening gates (per family)

1. Modern long-only liquid evidence (post-2010, prefer post-2015; large/liquid subsets;
   post-publication replication where available).
2. Public info before entry; persistence after next-tradable-open entry (announcement pop
   alone = REJECT_EXECUTION).
3. Natural ranking variable; weeks-to-months horizon; moderate slot-time/turnover.
4. Accessible PIT source with publication/revision/mapping/survivorship semantics.
5. Limited dependence on historical product taxonomy (else penalize/restrict/reject).
6. Concentration: literature expression size → top-six thesis → top-one thesis (never infer
   decile→top-one). Long-only leg separated from short-leg spread.
7. V3 distinctness (informational, not just holdings overlap).

## Dispositions

`SHORTLIST` | `DEFER_EXTERNAL_DATA_BURDEN` (hypothesis kept, procurement-burden deferral) |
`REJECT_EVIDENCE` | `REJECT_CONCENTRATION` | `REJECT_EXECUTION` | `REJECT_DATA_PROVENANCE` |
`REJECT_REDUNDANT`.

## No-peek enforcement

Scouts instructed: no repo-data computation; published paper results citable as external
evidence. Parent performed zero candidate-economic computation. Structural counts only for
constructability. Verification: `NO_ECONOMIC_PEEK_RECEIPT.json` + grep for forbidden
economic tokens in committed artifacts.
