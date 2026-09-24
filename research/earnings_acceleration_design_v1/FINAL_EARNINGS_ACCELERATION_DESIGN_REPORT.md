# Final design decision

`EARNINGS_ACCELERATION_DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`

Specific gate: `DESIGN_REJECT_NO_DEFENSIBLE_CONCENTRATED_EXPRESSION`. This means the **concentrated ParayaBuck implementation** lacks external support. The published diversified earnings-acceleration relation is credible; the data gaps below are separate blockers and are not an alpha rejection.

## Starting main SHA

Actual clean `main`: `7724a2bc9e61ba325a6a252c8f518cabd99d6c69`. Request expected `be440821e34d859f2e693c45532566eb0dafb1f8`; main had advanced. Branch was created from actual main; no concurrent branch was overwritten.

## Primary literature and exact definition

[He and Narayanamoorthy (2020)](https://doi.org/10.1016/j.jacceco.2019.101238) studies a second difference in year-over-year EPS changes. The [author draft](https://www.naaim.org/wp-content/uploads/2018/05/2018_00M_Earnings-Acceleration-and-Stock-Returns_NAAIM-Submission-with-title-page.pdf), section 2.1/Table 1, defines `EPS_t = IBQ_t / CSHOQ_t` with split/dividend-adjusted shares, `EGP_t=(EPS_t-EPS_(t-4))/P_(t-1)`, and `EAP_t=EGP_t-EGP_(t-1)`. Negative earnings remain valid; required quarters are `t,t-1,t-4,t-5`. Income-before-extraordinary-items divided by quarter shares cannot be silently replaced by generic net income or a tagged basic/diluted EPS field. Fiscal period end and paper announcement date differ.

## Economic mechanism and distinction

The authors argue investors underweight EAP's implications for growth two and three quarters ahead. EAP subtracts two adjacent seasonally differenced EPS growth measures. PEAD/SUE uses one seasonally differenced level; earnings-announcement return, analyst surprise, guidance, profitability and revenue acceleration use different information. Prior ParayaBuck event-family closures do not by themselves close this accounting hypothesis.

## Long-high evidence and modernity

Author draft Table 2 reports high-decile one-month/quarter-long market-adjusted returns of 1.6%/4.2% equal weighted and 0.8%/0.8% value weighted. The value-weighted quarter-long high-leg t-statistic is 1.921. The headline 1.8%/3.4% results are spreads. The original study's post-2003, <$5 exclusion and <$0.5B exclusion evidence emphasizes spreads, not a few-name long leg. `NO_MEANINGFUL_MODERN_CONFIRMATION` applies to post-2015 U.S. concentrated EAP. A [Swedish independent study](https://arc.hhs.se/download.aspx?MediumId=4821) using 2004-2016 data reports a weak/reversed high leg; it does not prove U.S. decay but limits external confidence.

## SEC source design and earliest causal availability

[SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) provide submissions, Company Facts/Concepts, and nightly bulk archives. Company Facts covers standard entity-wide XBRL facts and is a current aggregation. Item 2.02/EX-99 earnings releases can precede 10-Q and need direct extraction; 8-K cover-page iXBRL does not prove tagged earnings tables ([SEC Inline XBRL](https://www.sec.gov/data-research/structured-data/inline-xbrl)). [SEC FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions) states acceptance-to-public-document lag can be 1-3 minutes or longer, with no exact first-public timestamp. In cached records, seven of 329 visible Item 2.02 rows have filed date one day after UTC acceptance date. `available_at` must be based on proved public observation of every input. No earlier 8-K signal may use a future 10-Q value.

## XBRL, Q4, vintage, and mapping

No standard XBRL concept hierarchy is approved as equivalent to IBQ/CSHOQ. Q3 YTD is not discrete Q3. A Q4 `FY - 9M` income bridge is conditional on both vintage and share basis; annual-minus-YTD EPS is unsafe. Amendments/restatements must remain separate filing vintages with accession, acceptance, unit, context and period. The canonical v5 lifetime file has no CIK; current SEC ticker matching cannot establish historical identity. Multi-class, delisted, foreign, ADR, SPAC/shell and fund cases fail closed without dated evidence.

## Historical source coverage and structural supply

`STRUCTURAL_EVENT_SUPPLY.json` reports an offline diagnostic on 14 cached large issuers, recent submissions arrays only. Visible Item 2.02 counts rise from 5 in 2016 (two visible issuers) to 50 in 2025 (14 visible issuers); **none** of those event accessions has an entity-wide standard Company Facts fact. This is not a full sample or proof that an EX-99 release lacks the value. Earlier archived submissions segments, custom facts, release tables, exact paper inputs, first-public timestamps, year-by-year market denominators, amendments' economic effects, and mapped eligible securities were not audited. No per-event EAP, percentile, liquidity eligibility, weekly supply, clustering, or slot-time statistic is claimed.

## Concentration, V3 overlap, and holding horizon

The paper shows an across-decile trend; its regression uses decile ranks. It provides no within-top-decile evidence for selecting six or fewer names. Eight global slots with up to two crypto leaves at most six for equity, shared with V3. `V3_STRUCTURAL_OVERLAP.json` is `NOT_MEASURABLE_WITH_CAUSAL_INPUTS`; contemporaneous collision counts would require verified source and identity joins. The primary day 2-30 month window and alternate to next-announcement-plus-one are described, but the exact session/open/close convention remains unverified. Shortening horizon for slot fit is unauthorized.

## Public-data policy status

[Current policy](../../docs/research/public_data_research_policy_v1.md) permits EDGAR and canonical data but requires causal source and mapping proof. Commercial inputs are not being procured. Public exact reconstruction has not been ruled out; neither has it been demonstrated. This is not `DEFER_EXTERNAL_DATA_BURDEN`.

## New local economics calculated

`NONE`

## Production files changed

`NONE`

## PAPER/LIVE changes

`NONE`

## Frozen candidate

`NOT AUTHORIZED`

## Exact next action

`KEEP_EARNINGS_ACCELERATION_CLOSED`

## Final branch SHA

See the branch HEAD at handoff; it is recorded in the final response after the research-artifact commit.
