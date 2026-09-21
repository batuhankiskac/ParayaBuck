# Final source reassessment

## Reassessment decision

`REASSESSMENT_CONFIRMS_DESIGN_REJECT`

Official SEC history and a conservative fail-closed mapping/classification
subset are feasible. The design nevertheless remains rejected because modern
liquid long-only evidence is insufficient and the one-slot ranking rule is not
externally defensible. A working downloader does not create alpha.

## Previous rejection preserved

This branch started from clean `main`/`origin/main` commit
`d3dea037789c16c335744b56d216289a4536096c`. The prior rejection remains
unchanged at branch `research/opportunistic-insider-purchases-design-v1`, commit
`d08c94cd93a1f71e9f9b8bf21e3c726b73953f50`.

## SEC access recovery

A compliant declared User-Agent recovered 53 official quarterly archives from
2013-Q1 through 2026-Q1. The operator-supplied contact was used only in request
headers and is not persisted in tracked artifacts. The 2026-Q2 Data.gov-listed
SEC resource remains an explicit gap because `dcm.sec.gov` could not be
resolved in this environment. Every acquired ZIP has a committed filename,
size, and SHA-256 receipt.

Accession `0000900440-16-000170` was traced from 2016-Q1 bulk rows to its SEC
index, complete submission, and primary XML. Issuer CIK/symbol, owner CIK, and
both purchase rows agree; object hashes are in `SEC_ACCESS_AUDIT.md`.

## Alpaca capability

`ALPACA_NOT_A_FORM4_SOURCE`. Current Alpaca documentation exposes market data,
assets, corporate actions, and trading—not historical Form 4 filings,
reporting-owner CIK, ownership accession, footnotes, or raw ownership XML.

## Alternative provider comparison

sec-api.io can be a useful normalized/raw-package cross-check; Form4API is too
recent and its amendment rewriting is unsuitable as the sole PIT record;
Knowable is a recent validation corpus only. None overrides SEC raw filings.

## Official SEC schema

The bulk packages provide accession, filing/form metadata, issuer CIK/name/
as-filed symbol, reporting-owner CIK/name/relationship/title, Table-I
transaction title/date/code/direction/shares/price/ownership, footnotes, and—
where historically available—the filing-level 10b5-1 flag. Exact field names
and limitations are in `SEC_SCHEMA_AUDIT.md`.

## Security mapping result

`INSIDER_SECURITY_MAPPING_FEASIBLE_FAIL_CLOSED` for a bounded repository
subset. Exact as-filed symbol plus filing-date lifetime validity must yield one
entity; the as-of CIK/entity bridge must remain unique; the security title must
match an explicit common-like pattern; and the filing must have exactly one
owner for CMP attribution. No current-symbol alias is used.

The scan read 2,740,318 submissions and 2,491,732 Form 4/4-A submissions. It
found 399,104 positive original-Form-4 Table-I code-P acquisition rows. Of
these, 103,681 have one exact symbol/date lifetime match, 81,312 also have a
common-like title, and 57,243 pass the as-of identity and single-owner gates.
That is 14.34% of all qualifying SEC purchase rows and must not be represented
as full-EDGAR coverage.

## CMP classification feasibility

`CMP_CALENDAR_CLASSIFICATION_FEASIBLE_FAIL_CLOSED`. At each year start, owner
CIK state uses only transaction months from the prior three complete calendar
years whose original filing date was already public. Activity in each year is
required; a month common to all three yields absorbing routine status,
otherwise opportunistic. Insufficient history stays unclassified.

Multi-owner rows never enter owner history. From an owner's first public 4/A
onward, the owner becomes `amendment_ambiguous` because bulk data cannot safely
identify which original row was replaced. This is deliberately conservative;
it is not a claim of complete CMP sample replication.

## Transaction semantics

Code P means an open-market **or private** purchase. Table I, direction A,
positive shares/price, title checks, and footnote/raw-file review narrow the
population but do not turn P into an exchange-only conviction flag. The
post-2023 10b5-1 field cannot be used as a symmetric full-history exclusion;
no such exclusion is frozen. Role filters and a purchase-policy candidate also
remain unfrozen.

## Structural event coverage

The causal, amendment-fail-closed state produces 2,649 eligible purchase
transaction rows from 2017-01 through 2026-03: 1,659 accessions and 1,225
distinct entity-month opportunities across 447 entities and 560 opportunistic
owners. All 111 months contain at least one row; median monthly supply is 19,
p90 is 47, and the range is 1–147. Median filing lag is one calendar day and
p90 is four. Tail lags remain and would require an explicit future execution
rule.

These are source-arrival counts, not returns. Multiple transaction rows within
one filing are not mislabeled as distinct issuer opportunities in the summary.

## Modern liquid long-leg evidence

`MODERN_LIQUID_LONG_LEG_EVIDENCE_INSUFFICIENT`. CMP supports an old-sample
purchase leg, but its sample ends in 2007 and the 0.82% headline is buy-minus-
sell. Later work reports post-Dodd-Frank decay, while a 2018–2023 public-filing
study finds adverse liquid-quartile and dollar-capacity evidence before costs.
That does not establish a modern, scalable, liquid, public-copy long leg.

## One-slot ranking evidence

`ONE_SLOT_RULE_NOT_DEFENSIBLE`. CMP supports opportunistic-purchase count as a
predictor in diversified tests, not ranking simultaneous issuers and investing
only in the top one. Full monthly occupancy cannot validate a winner-take-all
transformation or the account-wide 5+1 constraint.

## No-economic-peek result

`NO_ECONOMIC_PEEK_VERIFIED`. The structural program opens SEC event tables and
the lifetime file, but no market-price bar file. It computes no future return,
P&L, NAV, Sharpe, drawdown, hit rate, IC, bucket result, parameter performance,
transaction-cost backtest, or V3 outcome correlation.

## Final reasoning

The prior source-access concern is resolved for a causally complete historical
window, and the old blanket mapping blocker can be narrowed to a measurable
fail-closed subset. Those improvements do not cure the independent evidence
failures. Because either insufficient modern long-leg evidence or an
unsupported one-slot rule requires confirmation of rejection, no V2 is
authorized.

## Production state

Unchanged. No strategy, risk, ingestion, broker, PAPER, or LIVE behavior was
modified. No candidate, economic test, combination test, PAPER promotion, or
LIVE promotion is authorized.

## Files

All requested artifacts are present in this directory. The two structural JSON
files are reproducibly generated by `run_structural_reassessment.py`; external
ZIPs and raw samples remain in the ignored research cache.

## Branch SHA

The immutable final SHA is reported in the delivery message. It cannot be
self-embedded because changing this file changes the commit hash.
