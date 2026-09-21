# SEC quarterly schema audit

The following headers were read directly from the packaged official TSVs. Names
are preserved exactly, including abbreviations and legacy spelling.

## `SUBMISSION.tsv`

`ACCESSION_NUMBER, FILING_DATE, PERIOD_OF_REPORT, DATE_OF_ORIG_SUB, NO_SECURITIES_OWNED, NOT_SUBJECT_SEC16, FORM3_HOLDINGS_REPORTED, FORM4_TRANS_REPORTED, DOCUMENT_TYPE, ISSUERCIK, ISSUERNAME, ISSUERTRADINGSYMBOL, REMARKS`

Recent packages may also carry `AFF10B5ONE`. Its absence in older periods is
unknown, not false.

## `REPORTINGOWNER.tsv`

`ACCESSION_NUMBER, RPTOWNERCIK, RPTOWNERNAME, RPTOWNER_RELATIONSHIP, RPTOWNER_TITLE, RPTOWNER_TXT, RPTOWNER_STREET1, RPTOWNER_STREET2, RPTOWNER_CITY, RPTOWNER_STATE, RPTOWNER_ZIPCODE, RPTOWNER_STATE_DESC, FILE_NUMBER`

## `NONDERIV_TRANS.tsv`

`ACCESSION_NUMBER, NONDERIV_TRANS_SK, SECURITY_TITLE, SECURITY_TITLE_FN, TRANS_DATE, TRANS_DATE_FN, DEEMED_EXECUTION_DATE, DEEMED_EXECUTION_DATE_FN, TRANS_FORM_TYPE, TRANS_CODE, EQUITY_SWAP_INVOLVED, EQUITY_SWAP_TRANS_CD_FN, TRANS_TIMELINESS, TRANS_TIMELINESS_FN, TRANS_SHARES, TRANS_SHARES_FN, TRANS_PRICEPERSHARE, TRANS_PRICEPERSHARE_FN, TRANS_ACQUIRED_DISP_CD, TRANS_ACQUIRED_DISP_CD_FN, SHRS_OWND_FOLWNG_TRANS, SHRS_OWND_FOLWNG_TRANS_FN, VALU_OWND_FOLWNG_TRANS, VALU_OWND_FOLWNG_TRANS_FN, DIRECT_INDIRECT_OWNERSHIP, DIRECT_INDIRECT_OWNERSHIP_FN, NATURE_OF_OWNERSHIP, NATURE_OF_OWNERSHIP_FN`

## `FOOTNOTES.tsv`

`ACCESSION_NUMBER, FOOTNOTE_ID, FOOTNOTE_TXT`

## Semantic contract

- Accession is the filing identity; original and 4/A accessions are append-only
  public states, never retroactive edits.
- `RPTOWNERCIK` is the primary owner identity; names never merge identities.
- Code `P` means an open-market **or private** purchase. It does not prove an
  exchange trade or conviction signal.
- Table I, acquired flag `A`, positive shares, positive price, and a safely
  classified common-equity title form the structural discovery gate.
- Footnote identifiers must be resolved against `FOOTNOTES.tsv` or raw XML when
  a field is qualified.
- Bulk filing date is not acceptance time and not first-observed time.
- Multi-owner filings are unsafe for owner-level attribution because the bulk
  transaction row has no reporting-owner foreign key. This study excludes them
  from CMP event classification.

Schema authority: the SEC packaged readme linked from the
[official data-set page](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets).
