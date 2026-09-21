# Final design decision

`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`

# Starting repository state

After `git fetch origin --prune`, local `main` and `origin/main` were both
`d3dea037789c16c335744b56d216289a4536096c`. This branch was created from that
clean commit as `research/cash-based-operating-profitability-design-v1`.

# Primary cash-based operating profitability definition

CbOP is operating profitability adjusted for operating working-capital
accruals and scaled by lagged total assets. It is not CFO, free cash flow,
EBITDA, gross profitability, ordinary OP, or accruals alone.

# Exact accounting formula

`OP_t = REVT_t - COGS_t - (XSGA_t - XRD_t)`

`CbOP_t = [OP_t - ΔRECT_t - ΔINVT_t - ΔXPP_t + Δ(DRC_t+DRLT_t) + ΔAP_t + ΔXACC_t] / AT_(t-1)`

All deltas are year-over-year and the denominator is prior-year total assets.

# Balance-sheet vs cash-flow specification decision

The balance-sheet definition remains the primary reference. The post-1988 CFS
alternative is a robustness/non-articulation diagnostic and is not substituted
as a rescue candidate.

# Modern/post-publication evidence

Independent work supports a diversified CbOP spread. The most directly
relevant modern large/liquid study reports post-2005 raw long-short and
long-minus-market results, but selection-adjusted estimates fall to 0.06% and
0.00% monthly. Verdict:
`MODERN_CBOP_LONG_LEG_EVIDENCE_INSUFFICIENT`.

# Big/liquid-stock long-leg evidence

Original Table 4 big-stock high-decile monthly excess return is 0.65% (t=3.41),
CAPM alpha 0.16% (1.98), and FF3 alpha 0.37% (5.39). The big high-minus-low
spread is materially stronger. This does not prove a modern concentrated long
leg.

# SEC source architecture

Raw accession files are authoritative. Submissions and Company Facts are
discovery/cross-check layers. Frames is prohibited as signal history. The
accession package must preserve raw facts, contexts, dimensions, extension
taxonomy/linkbases, header SIC, acceptance timestamp, URLs and hashes.

# Accession-vintage PIT model

Facts become available at raw-header acceptance time. Original 10-K and later
10-K/A are separate vintages; amendments never rewrite past decisions.
Current and comparative balances come from the same annual accession. Later
10-Q facts cannot update the frozen annual score.

# XBRL concept mapping

Every paper component uses a taxonomy-versioned semantic registry plus filing
presentation/calculation validation. A static tag alias list is insufficient;
custom and dimensional facts are absent from Company Facts.

# SG&A / R&D treatment

The target is `XSGA-XRD`. R&D is added back only when filing relationships show
it lies within the SG&A subtotal. If R&D is already a separate sibling, adding
it back would double count. Ambiguous combined/custom tags fail closed.

# Working-capital components

Receivables, inventory and prepaids increases are subtracted; deferred revenue,
AP and accrued-expense increases are added. Current and prior instants must be
semantically matched within the same accession.

# Missing-data semantics

`SEC_MISSINGNESS_REQUIRES_FAIL_CLOSED_EXCLUSION`. Only an explicit numeric zero
is zero. Missing/aggregated/custom/unknown facts are not silently zero-filled.

# Amendments / restatements

Amendments create prospective vintages only when they actually contain changed
financial facts. Part-III-only amendments do not replace the original financial
state. Separately filed prior-year values are retained as diagnostics, not
mixed into same-accession deltas.

# Fiscal-year handling

Actual non-December and 52/53-week fiscal dates are retained. 10-KT transition
periods are excluded; re-entry requires a later full, comparable 10-K.

# Financial-firm exclusion

`PIT_FINANCIAL_EXCLUSION_FEASIBLE`: raw filing-header SIC 6000–6999 is excluded.
Missing/malformed SIC fails closed; current SIC never backfills history.

# Security/entity mapping

`SECURITY_MAPPING_FEASIBLE_FAIL_CLOSED`: CIK, accession time, as-filed DEI
security title/symbol and one active entity-lifetime match are required. Ticker
alone or today's ticker is never sufficient.

# Structural data coverage

The 14-issuer pilot found 190 Company-Facts-linked 10-K accessions and 12 raw
latest iXBRL filings. No accession/document satisfied every narrow standard-tag
family; the exact prepaids concept had zero comparative coverage. This is a
semantic warning, not an economic result or a full-universe estimate.

# Formula reconstruction verification

Apple accession `0000320193-25-000079` was reconciled from raw inline-XBRL at
fact/context/value level. In USD millions, `XSGA-XRD=27,601`,
`OP=416,161-220,960-27,601=167,600`, and the known numerator before prepaids
and accrued expenses is `164,601`. Exact prepaids are absent and the available
other-accrued-liabilities concept is not proven equivalent to `XACC`, so CbOP
is persisted as null rather than divided by lagged assets. The JSON retains
raw lexical values, contexts, scales, signs, arithmetic, accession timestamps,
duplicate-consistency checks, issuer CIKs, URLs, hashes and taxonomy URIs. No
10-K/A or transition-year execution case was
available in the raw-latest mini-sample; those rules remain design semantics,
not runtime proof. No outcome data were consulted.

# Broad-US_EQUITY implications

US-GAAP 10-K, filing-time nonfinancial SIC and common-equity DEI checks remove
much ETF/ETN contamination. They do not solve the historical security master,
multiclass issuers, REITs, shells or ticker reuse. The existing broad universe
limitation remains.

# Annual timing / causality

Paper-faithful timing is final June exchange close, using prior-calendar-year
fiscal observations with the six-month lag, followed by first July open. The
calendar primitive exists; a research-only annual scheduler would be needed.

# Six-slot concentration feasibility

`CBOP_REQUIRES_DIVERSIFICATION_INCOMPATIBLE_WITH_ARCHITECTURE`. Published tests
use diversified deciles/factors; none validates top six. With two crypto
positions, the account-wide six-position limit leaves only four equity slots.

# Eventual one-slot complementarity thesis

No paper validates a top-one CbOP issuer or establishes extreme-tail alpha
density. A later 5+1 test is not authorized.

# Red-team findings

The decisive failures are weak modern long-only evidence and unsupported
concentration. Additional source risks include missing-zero incompatibility,
SG&A/R&D ambiguity, custom tags, working-capital aggregation, restatements,
M&A non-articulation, unavailable PIT market cap and incomplete security identity.

# Exact candidate

None. A rejected design does not freeze a candidate.

# Future standalone G1–G9

None. No economic experiment is authorized.

# No-economic-peek verification

`NO_ECONOMIC_PEEK_VERIFIED`. No local candidate return, P&L, NAV, CAGR,
Sharpe, Sortino, drawdown, IC, hit rate, return bucket, parameter-performance
sweep, or V3 outcome correlation was calculated.

# Production state

Unchanged. V3 equity and crypto remain in PAPER; LIVE remains disabled. No
strategy, scanner, risk, execution, broker or production fundamentals pipeline
was modified.

# Files

All requested artifacts are in this directory. `CANDIDATE_DESIGN.md` and
`FUTURE_TEST_PROTOCOL.md` do not exist because the decision is not READY.

# Final research branch SHA

The immutable final SHA is reported in the delivery message; it cannot be
self-embedded because changing this file changes the commit.
