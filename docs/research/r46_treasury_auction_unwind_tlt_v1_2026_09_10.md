# R46 Treasury-auction unwind TLT governance contract — 2026-09-10

Status: `FROZEN_PRE_OUTCOME`, research-only. Economic execution is `false`,
economic run is `false`, integration and promotion are `false`, and PAPER/LIVE
are untouched. This document freezes governance and data-boundary requirements;
it contains no economic result.

## Current decision

R46 is not backtest-ready. The existing 2026 historical interval cannot be
accepted as an untouched holdout: prior research designated and accessed that
interval, while local ignored artifacts and access history cannot establish
non-exposure. A future holdout must be prospective, independently custodied,
single-use, and released only after the final runner and statistical plan are
approved.

The machine contract is
`data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json`.
The source inventory contract is
`data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json`.
The custody requirements are
`data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json`.

## Artifact and test ownership

The research owner freezes the manifest and hypothesis before any outcome is
opened. The data steward owns the source matrix, source-document lineage,
point-in-time coverage counts, and data-only preflight; the portfolio-research
acquisition/preflight implementation owns its own scripts and must publish
their source hashes into this manifest. The runner owner and verifier owner
must pin their exact files in `source_provenance` and place both files in the
declared base commit. The independent custodian owns the encrypted holdout
root and append-only access log. The operator signs the freeze and final
`PRE_RUN_OK`; the independent reviewer signs the statistical and custody
checks. The dedicated validator and its fail-closed tests are
`scripts/research/validate_r46_holdout_custody_2026_09_10.py` and
`tests/research/test_r46_governance_contract_2026_09_10.py`.

This bundle owns governance files and the custody validator only. It does not
acquire data, run a backtest, write economic results, or alter portfolio or
runtime files.

## Frozen hypothesis and event rule

The candidate is long-only TLT or cash at a desired account weight of 0.025.
An event qualifies only when an original nominal 30-year Treasury issue or
reopening has both its original offering document and actual completion/result
document available by 16:15 America/New_York on the auction session. Original
tenor, not an exact remaining-maturity string, determines a reopening. Offering
amount, tail, bid-to-cover, yield, or price thresholds are not selection rules.

A qualifying event enters at the next regular-session TLT open and exits at the
fifth regular-session open after entry. Securities qualifying on one date form
one exposure; overlapping signals do not pyramid or extend the exit. Missing
publication, completion, identity, eligibility, or capacity skips the event
without delayed catch-up. Document corrections, amendments, duplicates, and
source versions remain explicit and ambiguous versions exclude the event.

## Source and runner boundary

The freeze records the base/source commit and hashes every incumbent/config
component needed by the candidate. The R46 runner, verifier, preflight, and
custody validator must also be present and hash-pinned in the base commit before
any holdout or economic output is opened. A current checkout hash added after
outcomes is not sufficient. The source map and exact command must include all
transitive imports, configuration, environment/dependency lock, and test/review
artifacts. Source commit identity and later result-recording commit identity are
separate.

The official Treasury result sample exposes no exact publication or availability
clock. The acquisition package therefore has no PDF parser and retains fetched
response bytes only as `RAW_UNADMITTED`; it does not admit event data from a
`%PDF-` signature or synthetic text fixture. The self-hashed source capability
receipt at
`data/research_receipts/r46_treasury_source_capability_2026_09_10.json` is bound
by path, raw file hash, canonical artifact hash, route status
`CAPABILITY_OR_PAID_BLOCKED`, and R46 admission status
`DATA_ACQUISITION_REQUIRED`. This receipt records route capability only and is
not PIT readiness.

The native one-ledger composite is mandatory. Separate NAV curves or an
analytical 95/5 blend cannot establish shared cash, fills, fees, position slots,
or capacity. The candidate must preserve the incumbent and fail closed on
unknown identity, unavailable capacity, unfilled exits, or missing accounting.

## Statistical status

Statistics are deliberately `BLOCKED_STATISTICS`. No 365-day, 60-active-day,
24-episode, Bonferroni, bootstrap, permutation, effect-size, or block-length
value is encoded here. The five-session event holding rule makes daily marks
clustered, and 24 non-overlapping episodes would imply about 120 holding
intervals before counting independent evidence. Minimums require a pre-outcome
power analysis using the event-level estimand, expected event rate, capacity
suppression, missingness, serial dependence, target effect, power, and desired
family-wise error. A statistician and the operator must approve the resulting
plan and family registry before the final freeze.

The 45 prior attempts are not presumed to be the final multiplicity family.
Outcome-seen variants, endpoints, and selection steps that informed R46 must be
enumerated first. A valid failure ends this contract; it does not globally
disprove the mechanism. A valid pass remains research-only and does not promote
the candidate.

## Permitted data-only preflight

Before holdout release, the data steward and engineer may check document
schemas, hashes, identity, correction/duplicate lineage, publication and
`available_at` cutoffs, timezone conversion, TLT listing/tradability, corporate
actions, session calendars, aggregate event frequency, and aggregate residual
capacity. Synthetic fixtures may test native-ledger and fail-closed behavior.

No preflight may emit candidate returns, NAV/P&L, fills, turnover, drawdown,
CVaR, Sharpe, p-values, event-date rankings, threshold tuning, best-cell
selection, or holdout bodies. A coverage count that cannot meet the approved
sample plan is `BLOCKED_INSUFFICIENT_EVENT_RATE`; it is not a reason to extend
the period or relax the plan after inspection.

## Custody and stop gates

The independent custodian seals an encrypted future data root and append-only
access log. The seal receipt binds the holdout ID, manifest hash, custody-contract
hash, data-root hash, access-log hash, boundaries, storage class,
operator/custodian/reviewer attestations, and one-release policy. Operator,
custodian, and reviewer must be distinct.
The custodian releases the holdout once after a signed `PRE_RUN_OK`. Any
unauthorized access, hash mismatch, changed source/gate/formula, mutable storage,
second release, or same-sample rerun is `INVALID`.

Attestation strings are not cryptographic proof. An external cryptographic
signature verifier is required; until that verifier is independently configured
and checks the exact sealed hashes, the contract is valid in shape but remains
`BLOCKED_SIGNATURE_VERIFICATION`.

Required pre-run statuses are all clear: `BLOCKED_STATISTICS`,
`BLOCKED_NO_HOLDOUT`, `BLOCKED_RUNNER_NOT_IN_BASE`, `BLOCKED_SOURCE_PIN`,
`BLOCKED_PIT_SOURCE`, `BLOCKED_NATIVE_LEDGER`, and
`BLOCKED_SIGNATURE_VERIFICATION`. No economic package may run until these
blockers are cleared by the appropriate owner and independent review. There is
no automatic PAPER/LIVE or promotion path.

Therefore no candidate may advance to the final backtest from this checkout.
The only admissible advancement is one separately frozen, power-justified,
operator- and statistician-approved package after prospective custody is
sealed, the runner and verifier are present in the base commit, and every
data-only and native-ledger stop gate is clear. A data-only pass, a historical
diagnostic, or a custody receipt alone never authorizes PAPER, LIVE, or
promotion.

R46 remains `DATA_ACQUISITION_REQUIRED` / `BLOCKED_STATISTICS_HOLDOUT_SOURCE_PIN`.
