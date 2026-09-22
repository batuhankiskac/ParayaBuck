# Final discovery report — public-data alpha discovery v3 — 2026-09-22 (pre-outcome)

## Starting repository state

- Fetched/pruned; local `main` = `origin/main` = `91adb80a6157a10457e1b526e4a96b7fb8f293f3`
  at task start; working tree clean; current branch at start:
  `research/security-master-vendor-outreach-v1`.
- All three campaign SHAs verified before acting: source-audit `7366ea51…`,
  entitlement-prep `951e6090…`, outreach `2d5b2b3d…`; production-path diffs
  (`src config scripts tests pyproject.toml Makefile setup.cfg`) empty on all three.
- Incumbents verified from main: `equity_residual_momentum_v3` (equity sleeve 0.55,
  `top_n: 8`, max name 0.10, weekly) + `crypto_low_variance_hysteresis_v1` (sleeve 0.05);
  account max 6 positions, gross ≤0.60, cash ≥0.40.

## Security-master campaign closure

- Final classification: `DEFERRED_EXTERNAL_DATA_NOT_WORTH_PROCUREMENT_BURDEN` — neither
  `SOURCE_INVALID` nor solved. NYSE lead was technically promising; original-vintage
  semantics stayed unproven from public docs; further proof needed vendor contact and
  commercial negotiation, which the operator intentionally declined.
- Archive tags (created, pushed, remote peeled targets verified):
  `archive/research/security-master-source-audit-v1-2026-09` → `7366ea51…`;
  `archive/research/security-master-entitlement-prep-v1-2026-09` → `951e6090…`;
  `archive/research/security-master-vendor-outreach-v1-2026-09` → `2d5b2b3d…`.
- Branches deleted (local + remote): all three security-master branches; process-only
  `maintenance/public-data-research-reset-v1` after merge. `git branch -a` shows only
  `main` + `origin/main` (+ discovery branch created later).
- Historical taxonomy limitation RETAINED and visible in `equity_universe_limitations.md`:
  historical `US_EQUITY` not proven PIT common-stock-only (ETF/ETN/preferred/warrant/
  right/unit/structured contamination possible). Practical rule: penalize/restrict/
  reject taxonomy-dependent candidates; never reopen procurement to rescue one.
- Outreach: `NO_VENDOR_CONTACT_MADE`, `NO_PURCHASE`, `NO_CONTRACT_ACCEPTED`,
  `NO_TRIAL_STARTED`. Nothing sent; nothing pending with NYSE.

## Public/free-data policy

- New durable policy `docs/research/public_data_research_policy_v1.md`: Tier 1 canonical,
  Tier 2 official public (EDGAR/FINRA/government/exchange-public/issuer filings), Tier 3
  genuinely open reproducible (provenance-gated), Tier 4 simple free API
  (dependence-documented). Commercial/sales-gated sources → `DEFER_EXTERNAL_DATA_BURDEN`.
- Exception rule (no absolute ban): commercial data reconsidered only if (1) very strong
  hypothesis FIRST, (2) dataset is the only blocker, (3) architecture fit, (4) value
  justifies effort, (5) operator explicitly authorizes. Until then public/free only.
- PIT rule: publication date/time, revision semantics, availability, effective date,
  mapping, survivorship, causality required; unknowns fail closed.

## New main SHA after governance cleanup

`82e534f10bba0c7afa0e08a2ba449b3953314c82` (pushed; local = origin). Governance commit:
register closure + inventory closure + limitations update + new policy. Discovery branch
`research/public-data-alpha-discovery-v3` created from this main.

## Public-data discovery methodology

Three parallel read-only scouts (13 SEC filing-event families; FINRA short-interest
feasibility + evidence; 18 corp-action/mechanics/text/opportunistic families), parent
integration, 15 closed families excluded without recycling. Full method:
`SEARCH_METHODOLOGY.md`. Web search was unavailable at parent verification (all providers
blocked) — scout citations are training-knowledge + prior repo reviews and MUST be
re-verified from full text before any design freeze.

## Long-list

32 families; dispositions: SHORTLIST 1 · DEFER 1 · REJECT_EVIDENCE 20 ·
REJECT_CONCENTRATION 6 · REJECT_EXECUTION 3 · REJECT_DATA_PROVENANCE 2 ·
REJECT_REDUNDANT 1. Full table: `LONG_LIST.md`.

## Shortlist

One gated nomination: spin-child post-distribution drift
(`equity_spinoff_child_drift_v1`, PROPOSED not frozen). Full record: `SHORTLIST.md`.
Second slot intentionally WITHHELD. Patent efficiency (D1) kept at principled
`DEFER_EXTERNAL_DATA_BURDEN`. FINRA short-interest surprise explicitly NOT promoted
(VW insignificance, subperiod instability, OOS deterioration, staleness, momentum overlap).

## Scorecards

A1: favorables 11/16 (C,E,F,H,J,K,L,M,N,O,P ≥4); fatal-risk B/D at 2 — GATED, not
cleared. No mechanical summation: B/D failure kills regardless. `CANDIDATE_SCORECARDS.md`.

## Recommended next public-data family

`RECOMMENDED_NEXT_PUBLIC_DATA_FAMILY: equity_spinoff_child_drift_v1`

## Why this family deserves the next design campaign

Issuer-specific neglected-child mechanism; naturally concentrated (10–30 concurrent →
top-6 fillable); months-long drift survives next-open entry; EDGAR PIT-clean (acceptance
datetime + regular-way dates, when-issued excluded); strongest taxonomy posture
(issuer-linked child CIK); V3-distinct by construction (children screen out of V3 on
history/ivol); free source, simple parameters, moderate turnover.

## Why other closest candidates did not

- D1 patents: real evidence but no free PIT assignee→ticker concordance after 2010 → DEFER.
- F-1 short interest: VW long leg insignificant (−5bp vs −53bp EW), stale biweekly prints,
  monthly turnover, momentum overlap → REJECT_EVIDENCE.
- S-C CEO/CFO buys, S-E 5.02: old/announcement-only, no post-2010 drift → REJECT_EVIDENCE.
- Everything else fails evidence/concentration/execution/provenance/redundancy outright.

## Data path

EDGAR bulk index (10-12B effectiveness) → parent 8-K/Ex-99.1 (ratio/record/distribution/
regular-way dates) → child CIK→ticker→entity assertion → 1 PIT row per spin (4 dates +
acceptance datetime) → month-end recency×liquidity rank → next-tradable-open entry.
Cross-check ex/regular-way vs canonical actions (non-cash-ratio extension scoped,
research-only). First step: full-text re-verification + post-2015 liquid-only update.

## Main unresolved risks

1. Modern liquid update fails (premium only in small/micro) — kills nomination.
2. Citation re-verification overturns the classical channel reading.
3. Regular-way/when-issued mistiming in the event-row spec.
4. Spin-ratio corporate-action path needs a loader extension (scoped risk).
5. Single-spin variance at top-1 (top-6 default mitigates).

## No-economic-peek statement

No forward returns, CAR, IC, quantile returns, Sharpe, CAGR, P&L, validation returns,
V3-outcome correlations, or optimized windows were calculated — by scouts or parent.
Structural facts used (print cadence, history depth) answer constructability only.
Receipt: `NO_ECONOMIC_PEEK_RECEIPT.json`. Forbidden-token grep over committed artifacts:
`forward return|CAR|information coefficient|\bIC\b|Sharpe|CAGR|P&L|backtest.*return|quantile.*return|correlation.*V3|Alpha.*[0-9]+\.[0-9]+%|net return.*[0-9]+%` —
matched only this report's own "No ..." sentences and the word "backtest" in the policy
prose (no computed values); per-artifact confirmation at validation.

## Production state

V3 unchanged; crypto unchanged; PAPER/LIVE unchanged; no new candidate registered
(proposed name only, nothing frozen, no strategy/config/registry change). Production-path
diff for the discovery branch contains research artifacts only (verified at validation).

## Final discovery branch SHA

Reported after commit and validation; a commit cannot embed its own SHA without changing it.
