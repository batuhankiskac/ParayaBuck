# Repeat-event and deduplication specification

This specifies research data hygiene, not a trading candidate or cooldown parameter.

1. Identify a document by issuer CIK, accession and document name; retain original bytes/hash and extraction version. Accession prefixes can identify a filing agent, so never derive issuer CIK from the accession prefix.
2. Build a program ledger keyed by issuer, security class and internally assigned stable program ID. Attach each disclosure as a time-stamped observation. Match explicit predecessor references, amount/currency, approval date, expiry and program language. Similar date or dollar amount alone is insufficient.
3. Collapse the same release reproduced in an 8-K, exhibit, 10-Q and 10-K into one program announcement with multiple provenance links. The later filing remains a later observation and never resets first-public time.
4. Record expansions as increments against a predecessor. Preserve both total and incremental amount; do not add the whole refreshed limit twice. A successor receives a linked ID, not independent-first-ever status. An unchanged renewal, extension or routine reaffirmation is a state update. Missing predecessor evidence yields unresolved status.
5. Simultaneous authorizations of multiple classes require explicit separate limits and historical security mapping; otherwise mark allocation ambiguous. An issuer can have multiple programs, but that does not create multiple independent issuer opportunities by assumption.
6. Amendments and corrections append records at their own public times. Preserve what was knowable originally; do not rewrite an earlier signal using later correction content. If the original claim cannot be reconstructed, exclude it from PIT eligibility.
7. Distinguish raw mentions, unique programs, unique announcement changes, first announcements, and issuers in any structural count. Report unresolved links and repeats explicitly. Manual adjudication must be outcome-blind and log its reason and evidence.

Examples: Microsoft 2016 is a successor described again in a 10-Q; Bank of America 2019 is an increment; Apple 2024 is an additional program in earnings. Source links are in the sample. No fixed lookback, cooldown or overlap holding rule is invented here. Historical program linkage can use later documents for audit reconciliation, but later-only knowledge cannot retroactively improve initial eligibility or ranking. Prior completion credibility may use only completion evidence public before the announcement; eventual execution cannot deduplicate away failed or uncompleted authorizations.

Acceptance proof before any economic use: duplicate releases do not multiply counts; same dollar limits on different programs are not collapsed; amendment times cannot backdate facts; renamed issuers retain identity; multi-class ambiguity fails closed; and missing histories stay unknown. These are future pipeline acceptance invariants, not claims that a pipeline was built or tested.
