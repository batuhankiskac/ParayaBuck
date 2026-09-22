# Final design decision

`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`

The public Schedule 13D long thesis fails before local economics. Primary and modern literature establishes a filing-centered revaluation, but not a robust unconditional return available after conservative public dissemination and a next-regular-open fill. Longer gains are materially dependent on future outcomes—especially takeovers—and no natural ex-ante rule supports concentrating overlapping campaigns into one slot.

# Starting repository state

The branch was created from clean `main` at `754f4060b1eb38593201b2a269e9593d083eb534`, equal to `origin/main` at audit start. PAPER was enabled; LIVE was disabled with zero cap. Existing V3, crypto, scanner, risk, persistence, and execution behavior were not changed.

# Schedule 13D economic mechanism

An outside blockholder crosses the beneficial-ownership threshold, accumulates while its thesis is private, and later discloses the position and purpose. Disclosure can reveal monitoring, governance, strategic, capital-allocation, or sale pressure. The market may capitalize expected changes immediately; later value can arrive through engagement outcomes.

The activist can own the pre-disclosure block. A public outsider cannot. The admissible hypothesis is therefore only the residual return after public dissemination, without conditioning on later success, takeover, exit, or reputation learned later.

# Primary activism literature

Brav, Jiang, Partnoy, and Thomas report a strong filing-centered effect, but their investable separation is much weaker: months +1 to +3 are positive only under equal weighting and null under value weighting. Greenwood and Schor show pronounced takeover dependence. Bebchuk, Brav, and Jiang reject long-run reversal but do not establish statistically reliable calendar-time alpha. Becht, Franks, Grant, and Wagner find value concentrated in disclosure and outcome months and weak/negative performance without outcomes.

Sources and exact interpretation are recorded in `PRIMARY_LITERATURE_REVIEW.md`.

# What return occurs before filing?

Broad windows such as [-20,+20] include activist accumulation, leakage, and the announcement response. They cannot be assigned to an outsider entering after publication. Deal-lifetime returns can begin before disclosure and therefore answer a different question.

This research did not calculate local event-window returns. It used published decompositions only.

# Outsider-investable post-filing evidence

The only notable short post-event evidence is an equal-weighted legacy result for months +1 to +3. The matching value-weighted estimate is null, later value-weighted windows are null, and the cited studies do not implement acceptance timestamp, dissemination lag, after-hours handling, and next-open execution. Long-run “no reversal” is not positive outsider alpha.

Verdict: `OUTSIDER_INVESTABLE_POST_FILING_LONG_LEG_NOT_SUPPORTED`.

# Modern evidence

Later publications largely use samples ending in 2010–2016 and filing-centered windows. They confirm activism relevance and absence of average reversal, but not a 2017–2026 causal next-open strategy. No reviewed peer-reviewed evidence tests the post-2024 five-business-day regime as an outsider portfolio.

Verdict: `MODERN_POST_FILING_LONG_ONLY_EVIDENCE_INSUFFICIENT`.

# Outcome dependence

Board changes, capital returns, restructurings, settlements, and strategic actions occur after entry and cannot define filing-time eligibility. International evidence places substantial value in outcome-announcement months; no-outcome campaigns perform weakly. Later outcomes may be attribution fields only, never selection fields.

Verdict: `ACTIVISM_RETURNS_REQUIRE_FUTURE_OUTCOME_INFORMATION`.

# Takeover dependence

Greenwood and Schor find acquisitions far more frequent among activist targets than controls and show strong returns among later-acquired targets but weak independent-target results. An ex-post “acquired” filter is direct lookahead; holding until acquisition is not a filing-time rule.

Verdict: `RETURNS_LARGELY_REQUIRE_FUTURE_TAKEOVER_OUTCOME`.

# SEC source / acceptance timing

The official reconstruction path is SEC daily/full/quarterly index discovery followed by immutable raw accession packages: accession index, complete submission, all documents and exhibits, hashes, retrieval receipts, and parser versions. Submissions JSON is not sufficient by itself.

Raw `<ACCEPTANCE-DATETIME>` is the best official event timestamp but is not a guaranteed first-public timestamp. The SEC records no first-public-availability timestamp. Any future causal implementation would apply a conservative dissemination lag and then select the first eligible regular-session open.

# 2024 regulatory regime change

Before 2024-02-05, initial Schedule 13D was generally due within 10 calendar days and amendments “promptly.” From 2024-02-05, initial filings are due within 5 business days and amendments within 2 business days after a material change. The 13D/G EDGAR cutoff moved from 5:30 p.m. to 10:00 p.m. ET.

Schedule 13D/G XML was voluntary from 2023-12-18 and mandatory from 2024-12-18. Historical acquisition must parse legacy ASCII/HTML and PDF/manual cases as well as XML. The shorter deadline can change leakage and timing but does not itself establish post-publication alpha.

# Initial 13D / amendment semantics

Initial and amendment identities come from exact SEC form types, not prose. A first 13D can be a 13G conversion and need not mark a new position. Amendments link through explicit citations or a unique deterministic match on issuer, covered class, and reporting-person overlap; ambiguous and orphan links fail closed. Group roster changes are preserved rather than silently split or merged.

# Activism classification

Schedule 13D is not synonymous with activist intent. Item 4 must be classified with exact evidence spans, affirmative-current-plan rules, negation/modality handling, amendment operation semantics, and a manual fail-closed queue. Boilerplate, “may consider,” reserved rights, and generic (a)–(j) recitations are not positive evidence.

# Filer identity

Subject issuer, EDGAR submitter/agent, cover-page reporting persons, beneficial owners, and reporting group are separate entities. Accession prefix and master-index metadata cannot enumerate the group. Activist reputation would require a versioned point-in-time registry and cannot be reconstructed from later success.

No filer universe is frozen under REJECT. The broadest scope examined is an external non-passive initial 13D filer with affirmative Item 4 evidence; the strongest papers are narrower hedge-fund-activist samples. A hedge-fund-only historical label is blocked without a point-in-time organization/fund registry.

# Security mapping

The required causal key is subject CIK plus exact covered class and CUSIP, mapped as of filing time into one canonical lifetime security. Current ticker substitution and issuer-only matching are prohibited. The repository lifetime source lacks CIK/CUSIP/class fields and broad `US_EQUITY` is not a complete PIT common-stock taxonomy.

Verdict: `13D_SECURITY_MAPPING_BLOCKED`.

# Structural opportunity supply

No returns were used. Thirty-eight completed SEC quarterly master indexes plus the partial current-quarter 2026 Q3 full index were hashed and counted from 2017-01-03 through the vintage ending 2026-09-21. The receipt preserves each canonical SEC URL/path, source kind, `Last Data Received` value, local-file mtime, byte length, and SHA-256. HTTP headers and an independently captured request timestamp were not retained, so local-file mtime is not represented as proof of retrieval time.

- 23,705 initial-form rows (`SC 13D` or `SCHEDULE 13D`).
- 82,369 amendment-form rows.
- 19,683 legacy initial rows and 4,022 XML-form initial rows.
- 13,524 unique index CIKs; 4,155 have multiple initial rows over the full interval.

The annual initial-row counts are 2,656; 2,566; 2,220; 2,288; 3,101; 2,319; 2,501; 2,184; 2,230; and 1,640 (2026 partial) for 2017–2026 respectively. Full monthly counts and every source hash are in `STRUCTURAL_OPPORTUNITY_AUDIT.json`.

These are form-row supply counts, not activist opportunities. Quarterly indexes do not contain Item 4, covered class, complete reporting persons, acceptance hour, or campaign linkage. Mapped/unmapped class counts, qualifying activists, acceptance-hour distribution, after-hours frequency, simultaneous live opportunities, repeat campaigns, and occupancy are explicitly `null`, not invented. Raw volume is ample; qualified one-slot supply remains unproven.

# Natural ranking rule

Ownership percentage, sale demand, activist reputation, target size, and campaign structure do not provide a validated monotonic, public, ex-ante one-slot selector. Some are ambiguous, some favor illiquid small firms, and some risk future information. No composite score was invented.

Verdict: `NO_DEFENSIBLE_CONCENTRATION_RULE`.

# Six-slot feasibility

The six-position limit is account-wide. With two crypto positions, at most four equity slots remain. Six equity names at the 0.55 equity allocation would fit the 0.10 name cap only in an equity-only view; it does not describe concurrent PAPER reality. Existing 5+1 governance is a research comparison policy, not deployment authorization.

# One-slot feasibility

One slot intensifies the missing ranking problem. “Take all qualifying filings” does not fit; choosing the largest stake, strongest demand, or best-known activist is unsupported. Sparse cash is acceptable but does not create a selector.

Verdict: `ONE_SLOT_13D_THESIS_UNSUPPORTED`.

# Daily execution compatibility

The generic engine can fill a completed decision at a later tradable open, but its configured cadence is monthly/weekly/`hours:N`; it has no EDGAR acceptance event scheduler. A research-only acceptance-to-dissemination-to-next-open scheduler would be required. More importantly, none of the core papers validates that conservative timing rule, and it may miss the documented announcement revaluation.

# Red-team findings

The design fails on three independent economic grounds: weak post-publication evidence, future outcome/takeover dependence, and absent ex-ante ranking. It also has unimplemented source, identity, classification, mapping, and event-scheduling requirements. Scanner attrition and liquidity are unresolved, while the strongest legacy effects skew toward smaller targets. All 24 required challenges are answered in `DESIGN_RED_TEAM.md`.

# Exact candidate

Not created because the decision is REJECT. `CANDIDATE_DESIGN.md` is READY-only.

# Future standalone G1–G9

Not created because the decision is REJECT. `FUTURE_TEST_PROTOCOL.md` is READY-only.

# No-economic-peek verification

No candidate prices, future returns, event-window local returns, P&L, NAV, CAGR, Sharpe, drawdown, hit rate, IC, parameter performance sweep, V3 outcome correlation, or future-return bucket was computed. The only local quantitative work counted SEC filing metadata. `NO_ECONOMIC_PEEK_RECEIPT.json` records the boundary.

# Production state

No production source, configuration, PAPER/LIVE state, V3, crypto, scanner, risk limit, persistence path, broker behavior, or registered strategy changed. No order was placed. No candidate was frozen and no future G1–G9 protocol was created because those artifacts are READY-only.

# Files

- `CURRENT_CAPABILITY_AUDIT.md`
- `PRIMARY_LITERATURE_REVIEW.md`
- `POST_FILING_INVESTABILITY_REVIEW.md`
- `MODERN_EVIDENCE_REVIEW.md`
- `SEC_13D_SOURCE_AUDIT.md`
- `REGULATORY_REGIME_AUDIT.md`
- `ACTIVISM_CLASSIFICATION_SPEC.md`
- `FILER_IDENTITY_AUDIT.md`
- `SECURITY_MAPPING_AUDIT.md`
- `TAKEOVER_OUTCOME_DEPENDENCE.md`
- `STRUCTURAL_OPPORTUNITY_AUDIT.json`
- `RANKING_RULE_REVIEW.md`
- `ONE_SLOT_FEASIBILITY.md`
- `CAUSALITY_AUDIT.md`
- `DESIGN_RED_TEAM.md`
- `NO_ECONOMIC_PEEK_RECEIPT.json`
- `DESIGN_DECISION.json`
- `run_structural_audit.py`
- `FINAL_DESIGN_REPORT.md`

# Final research branch SHA

The final commit SHA is reported in the handoff. A Git commit cannot truthfully embed its own SHA because changing the file changes that SHA. Starting SHA: `754f4060b1eb38593201b2a269e9593d083eb534`.
