# Final government-contract award design report

## Final design decision

GOVERNMENT_CONTRACT_AWARD_DESIGN_BLOCKED_DATA_OR_EXECUTION

## Starting origin/main SHA

be440821e34d859f2e693c45532566eb0dafb1f8

## Final research branch SHA

Recorded in the handoff after this report is committed.

## Primary paper

[Pyun (2025), Economics Letters 252, 112335](https://www.sciencedirect.com/science/article/pii/S0165176525001727). The accessible abstract and indexed fragments identify Quiver, September 2020–November 2023 and a positive large-contract association. Full methodology and tables were inaccessible from publisher, author page and ResearchGate. See PRIMARY_LITERATURE_REVIEW.md.

## Exact published signal

UNKNOWN. Award event definition, dollar field, aggregation, rank/breakpoints, rebalance, weights, lag and holding horizon cannot be reconstructed. No local candidate was created.

## Economic mechanism

New government demand, certification, backlog and lower uncertainty are plausible operating channels. They do not establish post-publication price drift. The paper's long-term association is suggestive but the first-public clock and exact tradable mechanism are unproved. Prior disclosure of down-selects, incumbent work, options and follow-on actions can reduce surprise.

## Long-only evidence

LONG_HIGH_EVIDENCE_LIMITED. The abstract claims positive cumulative large-contract strategies and market outperformance; high-leg estimates and significance are unavailable. A high-minus-low spread cannot substitute for the high leg.

## Modern/external support

LIMITED_EXTERNAL_SUPPORT. [Parajuli (2023)](https://academic.oup.com/raps/article/13/1/146/6575918) supports a different post-10-K government-customer signal. [Hebous and Zimmermann (2016)](https://www.imf.org/en/publications/wp/issues/2016/12/31/can-government-demand-stimulate-private-investment-evidence-from-u-s-43784) concerns investment. [Xu, Yang and You (2025)](https://www.nancyxu.net/_files/ugd/efd0b7_380b808e7ce7444da364c25d1cbf259b.pdf) locates return effects around later earnings. [TenderAlpha (2026)](https://www.tenderalpha.com/wp-content/uploads/2026/02/White-Paper-Defense-Contract-Announcements-TenderAlpha-HKU-1.pdf) finds no conventionally significant 3/5-day absolute-dollar top-tail result in its distinct proprietary global-defense sample.

## Quiver dataset interpretation

[Quiver's current documentation](https://www.quiverquant.com/datasources/) describes USAspending transactions, ticker mapping and a new-contract dashboard using total obligated dollars. Its [API page](https://api.quiverquant.com/datasets/government-contracts) distinguishes Date and action_date. Neither proves the paper's snapshot semantics or a first-public timestamp. No paid data acquired.

## Official public source landscape

FPDS CARs and USAspending transactions offer broad contract records but lag and can be revised. [SAM Opportunities](https://open.gsa.gov/api/get-opportunities-public-api/) has postedDate and award fields but its public API gives latest version. [SAM Contract Awards](https://open.gsa.gov/api/contract-awards/) has PIID, recipient/parent identifiers and current reveal fields, not a proven historical 2016 vintage. [Defense's archive](https://www.defense.gov/News/Contracts/) provides dated major-award text. Agency and issuer releases can be earlier, with fragmented coverage. No unified historical federal announcement corpus was proven.

## FPDS reporting delay

[FAR 4.604](https://www.acquisition.gov/far/4.604) allows three business days for ordinary complete CARs and 30 days for specified emergency actions. This is a reporting deadline, not the award's public-information date.

## USAspending publication delay

[Official data guide](https://www.usaspending.gov/data/about-the-data-download.pdf) describes next-morning transfer from FPDS and site publication the following day. Current backfilled records do not demonstrate their first public vintage.

## DoD public-data restriction

[DoD PGI 204.604](https://www.acquisition.gov/dfarspgi/pgi-204.6-contract-reporting) withholds public CAR data for 90 days after Date Signed; a [2016 archive](https://www.acq.osd.mil/dpap/dars/pgi/pgi_htm/r20161021/PGI204_6.htm) is search-indexed with the same rule. DoD's separate announcement channel may precede CAR release.

## Defense.gov announcement path

Dated contract pages and a longstanding [5 p.m. Eastern schedule](https://www.defense.gov/Today-in-DoD/Date/2014-4-16) support a conservative next-session clock for a page after earlier-release checks. Exact individual article posting time remains unproved. Pages mix initial awards, modifications, IDIQ, FMS, ceilings and obligations. A DoD-only design is not established as faithful to the broad primary hypothesis.

## Effective-dated announcement thresholds

General FAR: $4m through September 2020, $4.5m October 2020–September 2025, $5.5m from October 2025. DoD: $7m, $7.5m and $9m respectively. Rules, coverage and exceptions are in AWARD_THRESHOLD_HISTORY.md; no present-day threshold was projected backward.

## Award event taxonomy

Initial definitive and undefinitized contracts, modifications, options, orders, IDIQ vehicles, multiple-award vehicles, ceiling increases, incremental funding, de-obligations and definitizations are separate classes. Competition and incumbency are retained. Paper-specific included classes remain UNKNOWN.

## Contract amount semantics

Face/ceiling, not-to-exceed and obligated dollars differ materially in the five official sample rows. A [2026 $13.4bn Boeing ceiling modification](https://www.war.gov/News/Contracts/Contract/Article/4599224/contracts-for-sept-11-2026/) has zero new obligation. The exact Pyun ranking amount is UNKNOWN. No normalization by cap, revenue or assets is authorized.

## Recipient → parent → security mapping

Required UEI/CAGE to dated legal owner, listed issuer, historical security and canonical ParayaBuck entity_id chain was specified but not completed. Current parent names and fuzzy ticker matches fail closed. The canonical entity-lifetime file lacks government recipient identifiers. [SEC merger evidence](https://www.sec.gov/Archives/edgar/data/101829/000010182920000034/utx-20200331.htm) shows current Raytheon ownership cannot be applied to all historical dates.

## Multi-winner / JV handling

The [2016 17-winner IDIQ](https://www.defense.gov/News/Contracts/Contract/Article/1011584/) has one $11.4bn shared ceiling and only $56,463 combined initial obligations. A winner-specific amount is required; no parent receives 100% by default. Minority JVs and consortia fail closed absent allocation evidence.

## First-public available_at contract

Only archived, public and source-attributed publication evidence may set first_proved_public_at. Action date, submission, approval and current last-modified date remain separate. Date-only pages require a conservative next-session rule; earlier agency, issuer or notice releases must be checked. Later records cannot retroactively improve decision-time fields.

## Historical coverage

HISTORICAL_SOURCE_COVERAGE.json records five deliberately selected DoD announcement rows across 2016, 2020, 2023 and 2026. This shows source availability and regime/amount hazards, not full-year or federal coverage. Cross-source same-PIID matches, agency breadth, historical parent mapping and complete archive counts remain unverified.

## Source delay distribution

SOURCE_DELAY_DISTRIBUTION.json reports NOT_ESTIMABLE: zero verified same-PIID, same-modification records with both publication clocks. Policy deadlines are reported separately, never as observed lags.

## Structural event supply

STRUCTURAL_EVENT_SUPPLY.json reports NOT_ESTIMABLE. No paper-faithful event filter, mapped historical universe or holding horizon exists; sampled archive rows are not a population.

## Concentration feasibility

Not established. Continuous dollar size in the abstract does not justify six slots or a top-quantile cutoff. No tail supply, monthly unique issuers, clustering or occupancy was calculated from a qualifying population.

## Natural ranking strength

Absolute contract dollars are the base hypothesis only if confirmed in the full paper. Its continuous coefficient, monotonicity, breakpoints and large-cap definition are inaccessible. Relative-to-cap intensity is a separate hypothesis, not an authorized rescue.

## V3 overlap

V3_STRUCTURAL_OVERLAP.json reports NOT_ESTIMABLE; causal candidate event timestamps and issuer mappings are prerequisites. No V3 structural replay or complementarity return test ran.

## Holding horizon

UNKNOWN in the accessible primary material. No 5/20/60-day or quarterly holding rule was invented.

## Public-data policy status

Official and free sources exist, but the current [public-data research policy](../../docs/research/public_data_research_policy_v1.md) requires historical provenance, PIT availability and issuer mapping. The present evidence does not pass those gates. No paid Quiver or TenderAlpha data was bought.

## New local economics calculated

NONE

## Production files changed

NONE

## PAPER/LIVE changes

NONE

## Frozen candidate

NOT AUTHORIZED

## Future economic protocol

NOT AUTHORIZED; no test was run.

## Exact next action

Obtain a legitimate full Pyun article or author manuscript with the missing method/tables, then assemble versioned official same-contract source and historical ownership evidence. Revisit the design gates without inspecting local future returns. The precise current blockers are paper-method fidelity, first-public vintage, historical mapping and concentrated supply.

## Concurrent-work note

Starting from the fetched origin/main SHA, this campaign used only the isolated research branch. Whether origin/main advanced and the final branch SHA are recorded in the handoff after commit. No other agent worktree or branch was modified.
