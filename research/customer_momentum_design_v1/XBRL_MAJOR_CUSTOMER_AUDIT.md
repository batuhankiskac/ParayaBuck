# XBRL capability, not coverage measurement

Major-customer text blocks and dimensioned tables can expose a name or an anonymized member. This [2023 SEC rendered XBRL detail](https://www.sec.gov/Archives/edgar/data/57515/000005751523000017/R66.htm) shows `Walmart Inc.` and `McLane Company Inc.` members; other filings use `Customer A/B`. The [SEC companyfacts API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) explicitly limits aggregation to standard-taxonomy, entire-entity facts, excluding dimensional/custom identity contexts. Therefore raw filing/iXBRL inspection is needed even when a concentration amount is structured.

No year-by-year XBRL coverage, anonymous/named mix, or structured-amount accuracy was measured. A custom member is a filing-specific label, not a globally resolved public-company identifier.
