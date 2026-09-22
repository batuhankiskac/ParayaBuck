# Stage A cleanup result

Completed before Stage B. Both exit-audit annotated tags were pushed and exact
remote targets verified. Completed local equity V1 and crypto V2 branches were
deleted; the crypto V2 remote branch was deleted and the equity V1 remote was
already absent. V1 history was preserved. The unrelated local/remote
`research/schedule-13d-activism-design-v1` remains outside this closure.
Only the two requested durable research documents changed on main.

# Updated main SHA

`16db2eaad9061a1669166ef9ab6717853337fafc`, verified equal to origin/main.
Starting main/origin/main was the expected
`b81e9d9fef3744723449f4c019f2a5dd75f2e2cb`.

# Exit-audit durable conclusion

Equity and canonical crypto V2 both retain
`MIXED_PROFIT_RETENTION_BEHAVIOR`. Decisions are respectively
`EQUITY_EXIT_RESEARCH_NOT_CURRENTLY_JUSTIFIED` and
`CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED`. The specified episode/cohort numbers were
independently matched to persisted evidence at the exact audit SHAs. V1 crypto
remains historically `AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`, superseded by V2.

No alternative exit was tested. MFE is hindsight, not capturable profit.
V3 exits, crypto hysteresis, take-profit and trailing stops remain unchanged.
Reopening requires materially changed live/PAPER evidence, changed strategy
semantics or an independently justified new pre-outcome exit hypothesis.
See the [durable register](../../docs/research/research_register.md) and
[archive inventory](../../docs/research/research_archive_inventory.md).

# Starting buyback research state

Created `research/share-repurchase-announcement-design-v1` from clean updated
main only after archive/deletion/documentation verification and the passing
normal suite. Scope is public-source and design research only.
[Capability audit](CURRENT_CAPABILITY_AUDIT.md).

# Final design decision

**`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`**.

No sufficiently supported single announcement-time ranking and holding rule
was established for a modern liquid-stock concentrated long-only strategy.
Fixing data access would not resolve this independent hypothesis/architecture
failure. Additional source and mapping blocks are real but are not disguised
as an economic rejection. No local economic experiment was run or authorized.
[Machine-readable decision](DESIGN_DECISION.json).

# Primary buyback literature

The review covers Ikenberry–Lakonishok–Vermaelen, Peyer–Vermaelen and
Grullon–Michaely, with direct primary links and explicit access limits.
Classic evidence motivates research but cannot alone authorize this design.
[Primary review](PRIMARY_LITERATURE_REVIEW.md).

# Modern/post-publication evidence

Fu–Huang and Lee–Park–Pearson challenge persistence; Evgeniou and coauthors
provide contrary supportive findings with weighting/volatility limitations.
Ahmed and coauthors extend conditional observations through 2019. Publication
date is distinguished from sample date. No qualifying post-2020 US next-open,
liquid-stock, concentrated net-cost replication was verified. This bounded
search is not proof that no such paper exists.
[Modern evidence and access limitations](MODERN_EVIDENCE_REVIEW.md).

# Outsider-investable post-announcement long leg

Not sufficiently established for the requested implementation. Published
announcement windows, benchmark-adjusted long-window averages and issuer
execution advantages are not interchangeable with the first attainable open.
Positive historical evidence and modern disagreement are both preserved.

# Event definition

Public authorization of identified issuer common equity under an open-market
program. Separate actual execution, tender/Dutch auction, ASR, negotiated
transactions, completion, unchanged renewals, increments and successors.
First public does not mean first-ever issuer program. Broad mandates need
literature-consistent adjudication; no unsupported pure-only rule is imposed.
[Taxonomy](EVENT_DEFINITION.md), [repeat ledger](REPEAT_EVENT_DEDUP_SPEC.md).

# Source feasibility

`REPURCHASE_EVENT_SOURCE_BLOCKED` for a validated adequately covered corpus.
SEC records are a plausible source; full historical coverage was not proved.
The EFTS probe was denied and no configured SEC User-Agent was available.
Four primary examples demonstrate heterogeneous paths and retrospective
mentions, not event prevalence. [Source audit](SEC_SOURCE_AUDIT.md).

# Public timestamp

Prefer verified SEC acceptance/dissemination for attached announcements;
retain earlier releases and never reset their clock at later filings.
Only two sample acceptance instants were verified; no earliest-public instant
was established. First-open execution must strictly follow validated public
availability. [Timestamp audit](PUBLIC_AVAILABILITY_AUDIT.md).

# Security mapping

`REPURCHASE_SECURITY_MAPPING_BLOCKED`. The canonical 9,010-row lifetime file
has no CIK/share-class bridge. Require as-filed CIK, ticker, security title and
effective lifetime, with exactly one supported entity match. No current ticker
backfill. Operating-company restrictions help exclude funds but do not solve
the general security master. [Mapping audit](SECURITY_MAPPING_AUDIT.md).

# Future-completion dependence

`POST_ANNOUNCEMENT_RETURNS_NOT_DEPENDENT_ON_FUTURE_COMPLETION` is narrowly
scoped to defining historical unconditional entry cohorts without hindsight.
It does not certify modern alpha or independence from later corporate actions.
Future completion, repurchase intensity and takeover status are forbidden
initial selectors. [Completion interpretation](FUTURE_COMPLETION_DEPENDENCE.md).

# Structural opportunity supply

Four purposive issuer examples span 2016, 2019, 2024 and 2026; two verified
acceptance times are after 16:00 Eastern. All four canonical joins remain
pending, not failed attempts. No eligible-event census exists. Full year/month
counts, simultaneous active events, clustering and occupancy are null rather
than invented zeros. No six-position minimum is imposed. Standalone and
one-slot supply sufficiency remain unproved.
[Structural receipt](STRUCTURAL_OPPORTUNITY_AUDIT.json),
[sample provenance](PRIMARY_EVENT_SAMPLE.json).

# Natural ranking rule

`NO_DEFENSIBLE_REPURCHASE_CONCENTRATION_RULE`. Prior decline is the single
simple lead examined, but no qualifying concentrated implementation is
supported. No U-index blend or authorization/market-cap proxy is substituted.
PIT market cap would be an additional dependency for size-normalized mandates.
[Ranking review](RANKING_RULE_REVIEW.md).

# Slot-duration / slot-efficiency

No single holding rule is justified. A multi-year holding consumes substantial
slot-years; published averages do not establish the required extreme-ranked
issuer's alpha density. No horizon or opportunity-cost return was calculated.
[Slot audit](SLOT_EFFICIENCY_AUDIT.md).

# Six-slot feasibility

`REPURCHASE_PREMIUM_REQUIRES_DIVERSIFICATION` is the conservative evidence
classification: wider-portfolio findings cannot validate six names. It is not
a mathematical claim that fewer holdings can never profit.

# One-slot feasibility

Issuer specificity is a useful structural feature, but it does not prove a
one-slot thesis. No scarce slot or capital allocation is authorized.

# V3 distinctness

The input is a corporate disclosure rather than V3 price-history momentum.
Incremental return distinctness and complementarity remain unproved; no V3
score, outcome correlation or combination was used.
[Causality audit](CAUSALITY_AUDIT.md).

# Red-team findings

All 20 requested challenges are addressed, including earnings bundling,
unavailable PIT expectations, future takeover attribution, source completeness,
post-publication decay and slot duration. No READY decision was forced.
[Red team](DESIGN_RED_TEAM.md).

# No-economic-peek verification

No candidate forward returns, local CAR, P&L, NAV, CAGR, Sharpe, drawdown, IC,
hit rates, future-return buckets, parameter searches or V3 outcome correlations
were computed. Only identity metadata and source-sample counts were read or
aggregated. Published literature estimates and Stage A's existing exit results
are separately identified. Independent review is an artifact/diff audit plus
per-agent action attestations, not an OS-level access trace.
[Receipt](NO_ECONOMIC_PEEK_RECEIPT.json).

# Production state

No strategy, configuration, PAPER/LIVE, risk, persistence or broker action
changed. Stage A normal suite passed: 1,138 passed / 1 skipped / 2 warnings;
initial sandbox-only port failures disappeared with local port permission.
Stage B adds research documentation/JSON only; no economic runner or candidate
implementation. Static configuration remains PAPER and LIVE disabled; no live
account inspection is claimed.

# Files

All requested unconditional artifacts are in this directory. No
`CANDIDATE_DESIGN.md` or `FUTURE_TEST_PROTOCOL.md` exists because the decision
is not READY. G1–G9 are not frozen or evaluated, and neither `ALPHA_REJECTED`
nor `ALPHA_VALID_COMBINATION_UNTESTED` is assigned without an economic run.

# Final research branch SHA

The delivery message records the exact final commit enclosing this report and
verification receipts. It is deliberately not embedded as a self-referential
SHA inside its own commit. The research branch is separate from production main.
