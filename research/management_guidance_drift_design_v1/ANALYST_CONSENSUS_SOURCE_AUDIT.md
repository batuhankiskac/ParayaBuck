# Analyst consensus source audit

Verdict: **`PIT_ANALYST_CONSENSUS_REQUIRES_LICENSED_SOURCE`**.

This is an obtainable-product-path finding, not proof of local entitlement or
of an already validated 2016–2026 event/estimate join. No licensed dataset was
provided, acquired or queried in this campaign. Targeted source/config/data
searches found no canonical analyst EPS panel or estimate adapter. An optional
question about existing user access was sent; absent a supplied archive or
confirmed entitlement, no access is assumed and no purchase/contact is made.

## Correct the premise before selecting a product

The final published paper's main definition uses the pre-forecast **nonsplit
I/B/E/S Summary median**, not a specified latest-per-analyst 180-day rule.
See `FORECAST_SURPRISE_SPEC.md`. Do not purchase a convenient daily average,
apply an invented freshness filter and call the result the exact published
signal. The paper leaves operational vintage details unresolved; its own mean
versus median wording also requires explicit version authority.

## Primary provider evidence

| Path | Verified historical/semantic evidence | What remains to be proved for this campaign |
|---|---|---|
| LSEG I/B/E/S History and PIT | Current [quant brochure](https://www.lseg.com/content/dam/data-analytics/en_us/documents/brochures/lseg-data-for-quant-research-brochure.pdf) distinguishes historical time series, daily PIT snapshots, current feeds and daily means; US history extends to 1976, with product-dependent measures/delivery. | Exact NONSPLIT Summary license, median fields, vintage/correction policy, availability timestamps, fiscal-period identifiers and inactive-company coverage in the purchased extract. Historical depth is not proof of event-level PIT fidelity. |
| Historical I/B/E/S Summary | [Vendor 2009 manual, pp.6–9](https://www.library.kent.edu/files/IBES_Summary_History_User_Guide_December_2009.pdf) distinguishes fiscal periods and adjusted/unadjusted packages; summary history uses monthly production snapshots. | Use the historically available snapshot, not a later refresh merely carrying an old statistics date. Obtain current documentation and archive guarantees. Relative FY1/Q1 labels must resolve to absolute period ends at each vintage. |
| First Call/CIG successor guidance | [LSEG guidance catalogue](https://www.lseg.com/en/data-catalogue/company-data/ibes-estimates/guidance) offers comparable guidance from 1994 and as-reported history from 2002/2003 where available, with source, measure, period, effective and activation fields. | Confirm access to legacy coding and comparable analyst benchmark; a current guidance product is not automatically the original CIG extract. Its guide's mean-at-date field cannot silently replace a median. |
| FactSet Estimates PIT | [Provider whitepaper, pp.3–8](https://insight.factset.com/hubfs/Resources%20Section/White%20Papers/ID11996_point_in_time.pdf): local-midnight consensus snapshots from December 2009, no later corrections/dilutions inserted. It describes consensus-only 100-day/45-day windows and a methodology caveat before September 2017. | Adequate calendar depth, but vendor-window/basis differences mean this is not established as I/B/E/S Summary equivalence. The documented product is not an individual-estimate reconstruction. Research-date versus input-date and later corrections matter. |
| Bloomberg | [Company Financials, Estimates and Pricing PIT](https://professional.bloomberg.com/products/data/enterprise-catalog/cofi/) describes 17 years of history, daily snapshots, comparable guidance/consensus and enterprise Data License delivery. | Dataset-wide history is not field-by-field EPS history. Check exact median, same-day cutoff, revisions, split basis and fiscal-period semantics. No licensed sample was inspected. |
| S&P Capital IQ | [Provider fundamental-data catalogue](https://www.spglobal.com/market-intelligence/en/solutions/products/fundamental-data) lists estimates history from 1996, but its change-capturing Snapshot product begins August 2016, updating every two hours. | Pre-August-2016 PIT and the first prior-year distribution are not automatically covered. Preserve availability versus estimate-effective time; require median/basis comparability and a full coverage manifest. |
| Visible Alpha | The same S&P catalogue describes history from 2015, analyst-model detail and PIT estimates. [Product page](https://www.spglobal.com/market-intelligence/en/solutions/visible-alpha) makes analyst-detail access entitlement-dependent. | Confirm original model versions, median comparability, coverage of inactive firms and complete 2016 warmup. More granular data are not automatically a faithful benchmark. |
| Zacks / Nasdaq Data Link / WRDS | [Zacks history documentation](https://www.zackspro.com/historical.asp) lists broker estimate dates, type, analyst/broker and old/new values. [Nasdaq catalogue](https://data.nasdaq.com/search?query=estimates) labels consensus-history products premium; [Zacks consensus catalogue](https://zacksdata.com/datasets/consensus-data/) lists long historical coverage and licensed delivery. | Alternative institutional path, not a demonstrated free panel or vendor-equivalent definition. Reconstruct availability, deletions, estimator population, units and basis from a licensed sample before considering it. |

Vendor advertising is evidence of product existence, not a passed reproducibility
gate. Price quotes and access terms were not obtained; no invented cost estimate.
Commercial access may be expensive, but actual cost depends on license/scope.

## Free versus official sources

[SEC financial statement datasets](https://www.sec.gov/dera/data/fsds.pdf)
contain issuer-filed financial data, not the full historical cross-broker
expectation benchmark. SEC exhibits may quote a consensus occasionally;
that does not supply a complete, consistently defined panel or underlying
snapshot lineage. Issuer guidance itself does not create analyst consensus.

FINRA's [investor research page](https://syndication.finra.org/content/stock-investing-and-due-diligence)
describes analyst estimates but does not establish an exportable historical
PIT panel. Nasdaq's premium third-party catalogue must not be confused with
free present-day quote pages or exchange-authoritative expectations. No
complete free SEC/Nasdaq/exchange/FINRA/issuer panel suitable for the exact
signal was verified in the bounded source search. This is not a proof that no
public estimate is ever available.

Current Yahoo/Nasdaq consensus, actual EPS, prior-quarter EPS, previous
management guidance, later webpage captures and post-event analyst revisions
are prohibited substitutes. No such value was used.

## Required source acceptance contract before a future freeze

- Immutable vendor/package/version and entitlement scope; complete dated files
  or events with original content hashes and correction/deletion lineage.
- Separate analyst research/effective time, vendor activation time, snapshot
  cutoff, file publication time and local receipt time. An old research date
  cannot backdate data that arrived after the guidance disclosure.
- Explicit summary-versus-detail definition, median/mean field and population
  policy. The final paper does not authorize importing the prompt's 180 days.
- Exact fiscal year/quarter and period-end match; currency, basic/diluted,
  continuing-operations/non-GAAP adjustments and per-share split basis match.
- Retain historical security identifiers and as-of issuer links; include
  withdrawn/deleted coverage rather than today's surviving analyst universe.
- Reconstruct only values available strictly before the event. An end-of-day
  snapshot containing post-guidance revisions is inadmissible for that event.
- Structural missingness/lag/coverage tables, including prior-year breakpoint
  warmup; no economic outputs needed to establish these conditions.

Until these checks pass, surprise signs, extreme-positive membership and
candidate occupancy remain unknown. Public guidance collection alone does not
complete the signal.
