# Final design decision

`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`

# Starting repository state

After a successful fetch, clean `HEAD`, local `main`, and `origin/main` were all
`d3dea037789c16c335744b56d216289a4536096c`. The campaign branch was created
from that commit.

# Primary CMP definition

An insider becomes classifiable only after trading in each of three preceding
consecutive calendar years. A common traded calendar month across all three
years makes the trader routine; otherwise the eligible trader is
opportunistic. Insufficient-history insiders are unclassified. Routine status
is absorbing. Opportunistic insiders are rechecked annually and become
permanently routine if a later prior-three-year window establishes the
same-month pattern. Fully rolling and trade-level forms are robustness variants.

# Purchase long-leg evidence

CMP's old-sample opportunistic-buy leg is positive under equal weighting and
most value-weight factor models. The 82 bp headline is buy-minus-sell, not
buy-only. Value-weight opportunistic-minus-routine buy spreads are not
significant. The large-stock test is only a median market-cap split.

# Modern/post-publication evidence

Evidence is mixed and adverse to the precise mandate. Later work reports weak
post-2003 value-weight/large-firm purchase alpha, lower post-Dodd-Frank
opportunistic-purchase profits, and a modern public-filing copy strategy whose
percentage returns disappear or become negative under realistic dollar
capacity before transaction costs. Some generic buy/pattern results survive,
but they do not prove incremental CMP classification in 2017-2026 liquid names.

# SEC data coverage

Official quarterly ownership extracts cover January 2006 through June 2026,
enough prior history in principle for 2017 classification.

# Bulk data vs raw EDGAR

Bulk supplies accessions, dates, issuer/owner CIKs, as-filed symbols,
relationships, transaction tables/codes/shares/prices, ownership form,
footnotes, and amendments. It omits acceptance time and some metadata, retains
as-filed inconsistencies, and rounds documented price fields to two decimals.
Raw XML/header plus the submissions API must be authoritative.

# Public availability contract

Transaction date is never information time. The necessary order is acceptance,
public observation, completed decision, then regular-session execution. SEC has
no timestamp for first sec.gov availability. Historical acceptance is a proxy;
a live collector should use its own first-observed time.

# Form 4 transaction semantics

Code P means open-market or private purchase of a non-derivative or derivative
security. It is not an open-market conviction flag. Table I, direction A,
positive shares/price, and resolved footnotes narrow discovery but do not prove
listed common equity or exclude a private transaction.

# Amendments / duplicate handling

Accessions are append-only. Original and amendment affect state only from their
own availability. No retrospective correction is permitted. Filing transaction
rows and owner participation are separate; identical values across accessions
do not prove duplicate economic events.

# Rule 10b5-1 regime

The structured filing-level field applies from 2023-04-01. Earlier absence is
unknown; plan adoption date is free text. Excluding flagged events only after
2023 creates a structural break, while complete pre-regime reconstruction is
not possible. No exclusion is frozen.

# Insider identity and historical classification

Reporting-owner CIK is the only acceptable primary identity. Names do not
merge people. Multiple owners, entities, trusts, 10% owners, and firm changes
must remain explicit. The causal CMP state machine is reconstructible in
principle, but owner scope is not adapted in this rejected campaign.

# Security-class / issuer mapping

`INSIDER_SECURITY_MAPPING_BLOCKED`. The current 9,010-row lifetime source lacks
CIK and security-class/listing provenance. Current-ticker or as-filed-ticker
matching alone cannot prove issuer, class, or ticker-reuse identity.

# Structural opportunity supply

No qualifying opportunity count is reported. The official ZIP endpoint
returned HTTP 403 to a non-personal User-Agent, and counting raw Code P rows as
qualifying events would be false precision before transaction/security gates.
The null counts are recorded explicitly in `STRUCTURAL_OPPORTUNITY_AUDIT.json`.

# Natural signal-strength / ranking rule

CMP Appendix A9 supports issuer-month count of opportunistic purchase
transactions as a predictive variable. It does not establish that the top one
issuer or a small top-k portfolio preserves the result. Dollar size and
distinct-insider count are not substituted.

# One-slot feasibility

Not proven. The literature tests diversified portfolios, not a single scarce
slot. The six-position constraint is account-wide; crypto occupancy can reduce
equity capacity, so equity 5+1 success would not itself prove deployability.

# ParayaBuck execution compatibility

Monthly month-end/next-first-open execution is technically representable and
closest to CMP. Event-level acceptance execution lacks a native scheduler.
Neither is authorized because the evidence gate failed.

# Long-only evidence verdict

`LONG_ONLY_EVIDENCE_INSUFFICIENT`

# Universe/security-mapping verdict

`INSIDER_SECURITY_MAPPING_BLOCKED`

# Red-team findings

The information source is distinct from residual momentum, reversal,
information discreteness, abnormal volume, and volatility management. That is
not portfolio-diversification proof. Modern liquidity/scalability, incremental
CMP value, after-cost persistence, and one-slot efficacy are unresolved or
adverse; source semantics and mapping add independent blockers.

# Exact candidate

None. A rejected campaign does not freeze a candidate.

# Future standalone protocol

None. No economic experiment is authorized.

# No-economic-peek verification

`NO_ECONOMIC_PEEK_VERIFIED`: no new candidate returns, P&L, NAV, risk-adjusted
metrics, drawdown, IC, hit rate, bucket study, parameter sweep, or V3 outcome
correlation was calculated.

# Production state

Unchanged. V3 equity and the crypto sleeve remain in PAPER; LIVE remains
disabled; no strategy, risk, ingestion, or broker path was modified.

# Files

The campaign artifacts are the Markdown/JSON files in this directory. No
`CANDIDATE_DESIGN.md` or `FUTURE_TEST_PROTOCOL.md` exists because the decision
is not READY.

# Final research branch SHA

The immutable final SHA is reported by `git rev-parse HEAD` in the delivery
message. A commit cannot self-embed its own hash because changing this file
changes that hash.
