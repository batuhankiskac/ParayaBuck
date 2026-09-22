# Final design decision

`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`

The evidence warrants **closing the proposed concentrated strategy before any local outcome test**. A historical, diversified child-long effect exists in some studies, but no independently validated pre-entry rank selects six liquid children; recent post-2015 U.S. child evidence does not establish that expression. Source and structural proof also remains incomplete. This is a decision about the specified strategy, not a finding that all spin-off children have negative returns.

## Starting repository state

On 2026-09-22 the worktree was clean on `research/public-data-alpha-discovery-v3` at `f37820f11e35da1d360600d7adbf10f501e88f09`. Local and live remote `main` both resolved to `82e534f10bba0c7afa0e08a2ba449b3953314c82`; live remote discovery branch resolved to `f37820f11e35da1d360600d7adbf10f501e88f09` by `git ls-remote`. This branch, `research/spinoff-child-drift-design-v1`, was created from clean `main`, not from discovery. [Handoff receipt](DISCOVERY_HANDOFF_RECEIPT.json).

## Discovery handoff

Discovery nominated `equity_spinoff_child_drift_v1` and proposed 10–30 concurrent names, 1–2 arrivals/month, 6–12 month drift, EDGAR-first events, and `recency × liquidity`. Discovery explicitly lacked final web verification. All were treated as leads. The public/free-data and PIT rules in [repository policy](../../docs/research/public_data_research_policy_v1.md) governed this audit.

## Why this is materially different from generic spin-off research

The narrower question isolates **the child's ordinary share after public distribution and verified regular-way trading**, with entry no earlier than the first causally available next open. Parent and combined returns, announcement reactions, when-issued discovery, split-offs, carve-outs and later takeover selection are outside the expression. This distinction fixes the old broad family's event-stage ambiguity; it does not rescue a missing concentrated child-long hypothesis. [Event definition](EVENT_DEFINITION_AUDIT.md); [timing](EVENT_TIMING_AUDIT.md).

## Primary literature

[Cusatis, Miles and Woolridge (1993)](https://longrunplan.com/wp-content/uploads/2018/09/restructuring-through-spinoffs.pdf) study 146 pure tax-free U.S. children from 1965–1988, measuring from the **first trading-day close**. Their 36-month 33.6% figure is an equal-child mean excess over industry/size matched firms, not an annualized, value-weighted, liquid, or next-open return. Eighty-nine children initially traded OTC and takeover-involved firms drove much of the later abnormal performance. [McConnell, Ozbilgin and Wahal (2001)](https://business.purdue.edu/faculty/mcconnell/publications/spinoffs-ex-ante-2001-vol74.pdf) test 1989–1995: the 36-month child matched excess is −20.9% and statistically insignificant, though size/book-to-market benchmarks give a more favorable result. The apparent effect depends on benchmark, period, and horizon. [Primary review](PRIMARY_LITERATURE_REVIEW.md).

## Modern evidence

`MODERN_EVIDENCE_INSUFFICIENT`. The best later peer-reviewed child paper, [McConnell, Sibley and Xu (2015)](https://business.purdue.edu/faculty/mcconnell/publications/The%20Stock%20Price...2013%20jpm.2015.42.1.143.pdf), starts events in **2001–2012**, despite its 2001–2013 title and 2015 publication. It reports positive all-child evidence, not a post-2015 U.S. liquid/top-six cohort. A 2021 [open full-text U.S. study](https://www.nature.com/articles/s41599-021-00807-9) covers 2007–2017 but tests announcement and distribution-stage value, not later child ownership. A 2023 [full-text North American thesis](https://research-api.cbs.dk/ws/portalfiles/portal/98732261/1642425_MastersThesis_Final.pdf) includes 2000–2020 events, but its 11 large-cap child observations do not provide significant large-cap abnormal evidence or a six-name rule. [Modern review](MODERN_EVIDENCE_REVIEW.md).

## Long-only child evidence

The 2015 study's 153 children averaged 31.7% raw versus 14.6% size/book-to-market benchmark over **22 months after ex-date** (17.1 percentage-point difference); the distribution is right-skewed. This is a positive diversified child leg, separate from parent/combined or spread results. It does not establish a liquid selected-six leg, exact next-open capture, or costs. The classic 6/12-month matched child figures are −1.0%/+4.5%, neither significant. [Long-only review](LONG_ONLY_EVIDENCE_REVIEW.md).

## Investable return timing

Announcement, Form 10, effectiveness, record date, distribution, when-issued market, regular-way session and first ParayaBuck-executable open are eight separate clocks. The older first-day-close studies and the 2015 day-after-ex-date calculation do not verify a next-open fill. Public 2024 filings show `SOLV` regular-way on its April 1 distribution date, while `GRAL` regular-way started June 25 after June 24 distribution; date-plus-one arithmetic is invalid. No local return from an earlier clock was claimed. [Timing audit](EVENT_TIMING_AUDIT.md).

## Exact spin-off definition

The investigational cohort is pro-rata distribution, without holder consideration, of a U.S. operating child ordinary common share by a U.S. public parent, followed by national-exchange regular-way trading. Strict fully distributed/tax-free events best resemble older full-spin samples. Partial distributions (`SOLV` 80.1%, `GRAL` 85.5%), taxable spins, split-offs, carve-out IPOs, tracking stocks, merger-linked issues, rights, REITs, and foreign securities cannot be silently pooled. Completion and class identity must be known at entry, not selected by later success. No final event spec was frozen. [Audit](EVENT_DEFINITION_AUDIT.md).

## SEC/public source architecture

Actual SEC paths include child `10-12B` and `10-12B/A` registration and information-statement exhibits, plus parent/child `8-K` and exhibits, effectiveness evidence, and completion/trading notices. A Form 10 may be absent for an already reporting child, so no single form is exhaustive. Preserve filer CIK, accession, form, document URL, acceptance datetime, filing date, and each assertion's version. SEC says the acceptance timestamp is available but **first sec.gov visibility time is not timestamped**. A Solventum amendment was accepted March 5 at 21:27:09 while officially filed March 6. Issuer release timing needs its own archival proof; date-only is insufficient for same-day open. [SEC sources](SEC_SOURCE_AUDIT.md); [availability](PUBLIC_AVAILABILITY_AUDIT.md).

## Child security mapping

Required PIT chain: event → child legal issuer → child CIK → exact registered ordinary class → exchange/regular-way symbol and date → historical canonical entity. `SOLV` and `GRAL` can be hand-traced through public 2024 filings, but `GRALV` was a separate when-issued ticker and GRAIL later corrected schedule details. CIK alone is not a share-class identifier; current ticker metadata cannot backfill an old interval. Ambiguous mappings are `UNKNOWN/INELIGIBLE`. Full-period success/failure counts and canonical joins are unmeasured. [Mapping audit](CHILD_SECURITY_MAPPING_AUDIT.md).

## Historical taxonomy handling

The repo's historical `US_EQUITY` label remains insufficient to prove ordinary common stock. Contemporaneous issuer and listing evidence can establish a clean subset **case by case**; it does not exempt the candidate from proving every class or historical bar identity. No commercial security master was introduced. [Taxonomy audit](TAXONOMY_AUDIT.md).

## Structural opportunity supply

Canonical bars, entity lifetimes and corporate actions contain no spin parent-child relation, CIK, regular-way start or publication time. Corporate-action types include cash dividends, forward/reverse splits and stock dividends, but no spin-off type. Therefore verified event/year, child/event, concurrent children, mapping success, scanner attrition, six-slot and one-slot occupancy are **null/unmeasured**, not zero. External studies counted 146 historical U.S. events in 2001–2012 and 249 broader announcement-stage events in 2007–2017; neither is a canonical liquid, causally mapped inventory. The 10–30 simultaneous and 1–2 monthly discovery claims remain unverified for this design. Existing scanner concepts include $5 price and $20 million 20-day median dollar volume; they were not tuned or applied to a verified cohort. [Structural audit](STRUCTURAL_OPPORTUNITY_AUDIT.json).

## Concentration evidence

### Academic expression

The original studies average all children. The 2015 active all-child portfolio held **10–33 names** for 22 months; its mean advantage is right-skewed. No accessible primary child-specific ex-ante sort demonstrates a monotonic return rank. [Concentration audit](CONCENTRATION_FEASIBILITY.md).

### Top six

Choosing six would omit 40%–82% of that observed portfolio depending on occupancy. No published selection rule identifies which six capture the positive tail. Six-slot feasibility and liquid-name supply are also unmeasured. Top six is **unsupported**.

### Top one

No study establishes an ex-ante best individual child. A single issuer concentrates operating, financing, listing and takeover uncertainty and may occupy the only satellite slot for nearly two years. Top one is **unsupported**.

## Natural ranking

Focus change is a plausible binary event cohort, not a within-cohort six-name rank. Child size, distribution size, liquidity, forced selling, and insider purchases either lack demonstrated monotonic post-entry child alpha, concern the parent announcement stage, require unavailable PIT ownership, or arrive after the proposed entry. `recency × liquidity` has no primary validation and was not adopted. No weighted score was invented. [Ranking review](NATURAL_RANKING_REVIEW.md).

## Holding horizon

The older papers report 6/12/24/36 months; the 2015 paper's externally motivated all-child rule uses **22 months**, while 27 months is identified by the authors as hindsight optimal. The discovery's 6–12 month drift is not supported as a concentrated, investable window. No 1- or 3-month child-specific abnormal result was recovered from the foundational papers. [Horizon audit](SLOT_TIME_AUDIT.md).

## Slot-time assessment

Twenty-two months in one of six positions would require a validated way to choose and replace children as new events arrive. Event supply itself is not verified. Shortening to three or six months to save slot-time would change the hypothesis without evidence. A one-slot satellite is especially weak.

## V3 distinctness

`INFORMATIONALLY_DISTINCT` **conditionally for a verified newly listed child**: organizational separation information differs from V3's price/residual-momentum signal, and V3 requires 274 own daily bars before scoring. Seasoned carve-outs can already have that history. Actual overlap and portfolio complementarity were not measured. [V3 audit](V3_DISTINCTNESS_AUDIT.md).

## Execution feasibility

Normal listed shares and next-tradable-open orders fit the existing engine mechanically. Historical regular-way proof, exact share mapping and opening liquidity remain unverified. The production scanner's trailing history can delay eligibility, and existing generic cost assumptions are not post-spin spread measurements. No when-issued or intraday fill was assumed. [Execution audit](EXECUTION_FEASIBILITY.md).

## Red-team findings

Takeover-sensitive old results, the mixed ex-ante replication, a modern announcement-stage study, possible small/illiquid concentration, survivorship/delisting, WI leakage, future-outcome conditioning, class/ticker ambiguity, right-skewed diversified means and long slot occupation all challenge transfer to this strategy. [Independent red-team](DESIGN_RED_TEAM.md).

## Fatal blockers, if any

The decisive **hypothesis** failures are absent modern liquid selected-child validation, absent top-six evidence and absent a defensible natural pre-entry rank. They warrant rejection **even if** SEC source engineering later proves feasible. Separate unfinished capability gates are period-wide mapping, publication timing, event supply and scanner attrition. The decision is therefore a hypothesis rejection, not a claim that public source data is inherently impossible.

## Frozen design, if READY

None. No candidate specification, rank, portfolio rule or economic-run manifest was frozen.

## No-economic-peek statement

No local child forward returns, event CAR, MFE/MAE, P&L, Sharpe, CAGR, IC, V3-return correlation or parameter economics were calculated; no candidate backtest was run. Published external results were read solely to test transfer of the hypothesis. [Receipt](NO_ECONOMIC_PEEK_RECEIPT.json).

## Production state

V3, crypto, risk/configuration, PAPER/LIVE and production registration are unchanged. This branch contains research artifacts only. The completed project suite and tooling results are reported in the final task response; no unrun test is represented as passed here.

## Files

The 20 required research artifacts are in this directory: handoff receipt; primary, modern and long-only literature reviews; event definition and timing; SEC source and availability; child mapping and taxonomy; structural opportunity JSON; concentration, ranking and slot-time; V3 and execution; red-team; no-peek receipt; decision JSON; and this report. Since the decision is not READY, no frozen candidate spec exists.

## Final branch SHA

The final commit SHA is reported with the completed task response. A committed file cannot truthfully contain the SHA of the commit that contains itself.

## Exact next action

`CLOSE_AND_ARCHIVE_CANDIDATE`
