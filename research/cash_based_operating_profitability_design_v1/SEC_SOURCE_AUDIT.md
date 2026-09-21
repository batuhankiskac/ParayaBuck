# SEC source architecture

## Verdict

`OFFICIAL_SEC_ACCESSION_VINTAGE_FEASIBLE`

Authority hierarchy:

1. raw EDGAR accession package: index/header, complete submission, as-filed
   HTML/iXBRL, extracted instance, extension schema, and linkbases;
2. Submissions API/nightly `submissions.zip` for accession discovery;
3. Company Facts API/nightly `companyfacts.zip` for standard entity-wide fact
   discovery and cross-checking;
4. Financial Statement Data Sets for structured diagnostics;
5. Frames only for diagnostics, never historical signal state.

SEC documents that Company Facts includes non-custom taxonomy facts that apply
to the entire filing entity. It therefore omits precisely some custom and
dimensional facts required to reconstruct statement semantics. Frames selects
a last-filed best-fit fact and would violate accession-vintage history.

Official references: [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[developer/fair access](https://www.sec.gov/about/developer-resources),
[Financial Statement Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets),
and [Inline XBRL](https://www.sec.gov/data-research/structured-data/inline-xbrl).

XBRL adoption was phased in from 2009 through 2011, so SEC XBRL cannot recreate
the original 1963 Compustat history. A modern SEC implementation must openly
start later and must not claim original-sample identity.

## Acquired structural pilot

The ignored cache contains Company Facts and Submissions responses for 14
predeclared issuers and raw latest iXBRL/index files for 12 nonfinancial
issuers. The request identity was supplied by the operator and is not persisted
in tracked artifacts. `STRUCTURAL_COVERAGE_AUDIT.json` records every cached
file's SEC URL, path, CIK/accession where applicable, form, filed/accepted/
report timestamps, retrieval timestamp, byte size, SHA-256 and taxonomy URIs
where available.

The pilot is intentionally not a universe estimate. Its purpose is to test
semantic failure modes before any large crawl or economic outcome exists.
