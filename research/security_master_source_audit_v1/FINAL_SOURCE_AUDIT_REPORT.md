# Final source decision

`SECURITY_MASTER_SOURCE_LEAD_VALID_BUT_ENTITLEMENT_REQUIRED`

NYSE Group Security Master is the strongest single-feed lead. Its documented 2015-12-16 start, daily full/delta files, non-reused Stock Number and broad issue taxonomy make it technically credible. The audit cannot promote it to acquisition-ready because public evidence does not prove immutable original daily vintages, correction-arrival history, contractual backtest/retention rights or strict coverage of the 9,010 canonical identity intervals.

# Starting repository state

Local `main` and `origin/main` were both `91adb80a6157a10457e1b526e4a96b7fb8f293f3`; the working tree was clean and only `main` existed locally/remotely before creating `research/security-master-source-audit-v1`.

Canonical identities:

- bars `bb7aaae...5139`: 11,686,832 rows, 9,010 symbols, 2,659 session dates, 2016-01-04 through partial 2026-07-31;
- lifetimes `ed675d9b...4596`: 9,010 rows, 6,858 active / 2,152 inactive;
- corporate actions `a0a60755...c23`: 143,446 rows;
- venue labels: NASDAQ 3,783; NYSE 2,279; ARCA 1,783; BATS 937; AMEX 228.

# Current historical identity limitation

The repository proves provider UUID, symbol, one venue label, observed/valid interval, status and limited rename/terminal evidence. It does not prove effective-dated security type, issuer/CIK, issue identifiers, product subtype or original source vintage. `US_EQUITY` is therefore not ordinary-company-equity taxonomy. Full reconstruction: [CURRENT_IDENTITY_GAP.md](CURRENT_IDENTITY_GAP.md).

# Required target security-master contract

Separate issuer, issue and listing identities; effective time from knowledge time; append-only raw evidence with hashes; alias and predecessor/successor intervals; raw classification from eligibility policy; and fail-closed `UNKNOWN`/`CONFLICT`. CIK never substitutes for issue identity.

# NYSE Group Security Master

## History

Official v4.1 documentation states historical files from 2015-12-16, daily full/delta equity files, generation-dated SFTP/S3 directories and next-session start-of-day delivery.

## Vintage semantics

`VINTAGE_SEMANTICS_UNCLEAR`. Dated archives do not prove immutable as-first-distributed bytes or correction-arrival lineage.

## Venues

Primary Market identifies listing venue; traded flags identify NYSE Group books. The product claims all U.S. NMS securities approved to trade on NYSE Group platforms, including non-NYSE primary listings.

## Identifiers

Stock Number is documented as never reused after delisting; symbol, SIP symbol, optional CUSIP/ISIN and change-day old values supplement it. Some corporate actions allocate a new Stock Number. There is no CIK/stable issuer ID.

## Taxonomy

Security Type and Issuer Type support common, ADR, REIT, ETF/ETN/ETV, fund, closed-end fund, preferred, warrant, right, unit, structured product and unknown. Arca-focused fields support leverage/inverse and asset-class detail.

## Lifecycle

Daily A/M/D deltas and add/change/split/listing fields support reconstruction; relisting, merger succession and some corporate-action continuity still require explicit evidence.

## Corrections

No public immutable/revision policy was found.

## Licensing

SFTP/S3 are entitled. Historical bulk price, research/backtest, retention, termination and identifier rights are unresolved. `COMMERCIAL_QUOTE_REQUIRED`.

## Sample availability

Two public 2024 equity full files parsed consistently and demonstrated cross-market/type breadth; details/hashes are in [SAMPLE_DATA_AUDIT.md](SAMPLE_DATA_AUDIT.md). Samples do not prove continuous history.

## Suitability

Valid lead, not yet accepted.

# Nasdaq source path

Daily List supplies historical effective-dated events since 1999 and rich issue type/subtype/leverage/inverse fields; Fundamental Data supplies daily full Nasdaq-listed snapshots but public access promises only the current month. No permanent issue ID or original-vintage guarantee is documented. Current public directories are not historical evidence. Official 2024 pricing is $3,500/month/organization plus contractual/CUSIP requirements. See [NASDAQ_SECURITY_MASTER_AUDIT.md](NASDAQ_SECURITY_MASTER_AUDIT.md).

# Cboe / other venue source path

Cboe BZX history begins 2012-05-01 and supplies daily full listing plus event reports. It is BZX-primary, not all four Cboe trading books. It lacks a stable issue ID and explicit ADR/REIT/leverage coverage; vintage immutability is unclear. It is a fallback component for the 937 `BATS`-labelled lifetimes. No other listing venue is demonstrated as necessary by the canonical labels. See [CBOE_SECURITY_MASTER_AUDIT.md](CBOE_SECURITY_MASTER_AUDIT.md).

# Free / official supplemental sources

Original SEC filings can prove filed facts at accession/acceptance time; current ticker maps can bridge current CIK/name/ticker/exchange; Series/Class IDs strengthen fund class identity. None supplies a complete daily listing master. FINRA is mainly relevant to OTC transitions, outside the current canonical venue set. See [FREE_OFFICIAL_SOURCE_AUDIT.md](FREE_OFFICIAL_SOURCE_AUDIT.md).

# CIK bridge

Join dated exchange issue evidence to original SEC class/ticker/exchange facts and fund Series/Class IDs, retaining method, accession, knowledge time and ambiguity. Allow many issues per CIK and do not back-propagate current ticker maps.

# Original-vintage assessment

Every exchange lead is `VINTAGE_SEMANTICS_UNCLEAR`; only original EDGAR filing bytes are confirmed vintage for the specific filed facts. This is the decisive unresolved acceptance gate. See [VINTAGE_SEMANTICS_AUDIT.md](VINTAGE_SEMANTICS_AUDIT.md).

# Taxonomy coverage

NYSE and Nasdaq can derive the minimum taxonomy broadly; Cboe needs supplements for ADR/REIT and finer ETP attributes. Volatility/commodity/bond/crypto-related themes are only partially explicit. Unknown remains mandatory. See [TAXONOMY_FIELD_MATRIX.csv](TAXONOMY_FIELD_MATRIX.csv).

# Identifier stability

NYSE Stock Number is the only documented non-reused issue identifier among the serious leads. Nasdaq/Cboe require constructed identities using dated events and optional CUSIP. None publishes CIK. See [IDENTIFIER_FIELD_MATRIX.csv](IDENTIFIER_FIELD_MATRIX.csv).

# Canonical 9,010-entity coverage assessment

Strict historical matches: zero, because no entitled historical source or stable crosswalk was acquired. Against one public NYSE 2024 sample, 4,635 canonical symbols were exact SIP-symbol diagnostics and 4,375 were absent; none is accepted as an identity match. Historical ambiguity and ticker reuse are not measurable from that one-day join. All 9,010 still lack a source stable-ID bridge. See [CANONICAL_COVERAGE_ASSESSMENT.json](CANONICAL_COVERAGE_ASSESSMENT.json).

# Single-source versus multi-source decision

Test NYSE first because it claims cross-market NMS coverage and uniquely supplies a non-reused issue ID. Do not declare it sufficient until evaluation files pass. If it fails, use the explicit NYSE + Nasdaq + Cboe + SEC stack in [MULTI_SOURCE_ARCHITECTURE.md](MULTI_SOURCE_ARCHITECTURE.md).

# Licensing / entitlement findings

All exchange histories require entitlement for the needed proof. Rights differ materially, especially post-termination retention and CUSIP. See [LICENSING_ENTITLEMENT_AUDIT.md](LICENSING_ENTITLEMENT_AUDIT.md).

# Public pricing or commercial-quote requirement

Nasdaq: $3,500/month/organization in the official 2024 filing. NYSE and Cboe: `COMMERCIAL_QUOTE_REQUIRED`.

# Operator/vendor question list

Use the exact list in [VENDOR_QUESTION_LIST.md](VENDOR_QUESTION_LIST.md); do not purchase or accept terms under this audit.

# Risks / unresolved blockers

- Original daily vintage and correction arrival are unproven.
- Contractual backtest/storage/termination rights are unconfirmed.
- No strict 9,010-interval crosswalk exists.
- Historical schema evolution and source-field completeness may create interval unknowns.
- Ticker reuse, relisting and successor continuity must fail closed.

# Recommended next step

`REQUEST_COMMERCIAL_ENTITLEMENT_DETAILS`

Request written answers and an NYSE multi-date evaluation sequence before acquisition. Escalate to `PROCEED_TO_MULTI_SOURCE_SAMPLE_AUDIT` only if the NYSE lead fails coverage/taxonomy/identifier gates.

# Production state

Unchanged. No scanner, universe, strategy, risk, configuration, corporate-action, PAPER or LIVE behavior changed; no V3 backtest or universe migration was run; no closed candidate family was reopened.

# Files

This directory contains the required source, vintage, taxonomy, identifier, entitlement, sample, coverage, architecture, question, decision and final-report artifacts. Vendor sample bytes remain outside the repository.

# Final research branch SHA

The exact final tip is reported in the task handoff after commit and validation; a commit cannot embed its own SHA without changing it.
