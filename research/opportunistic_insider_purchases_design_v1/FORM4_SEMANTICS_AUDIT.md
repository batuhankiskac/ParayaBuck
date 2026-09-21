# Form 4 semantics audit

## Code P is not an open-market flag

Official Form 4 instructions define `P` as an open-market **or private**
purchase of a non-derivative **or derivative** security. Therefore
`transaction_code == P` cannot be named an open-market conviction purchase.

A discovery row must, at minimum, be a Form 4/4A Table I
`nonDerivativeTransaction`, Code P, acquired (`A`), with finite positive shares
and raw price, issuer CIK, owner CIK, and resolved footnotes. Its honest label
is `open_or_private_purchase` unless filing text affirmatively establishes the
narrower venue/type.

Table II P rows, options, warrants, conversions, exercises, grants, gifts,
plans, units, ambiguous consideration, and missing/zero-price rows fail closed.
This still does not prove the Table I security is the listed common share.

The primary CMP work used Thomson open-market purchase/sale categories; this
campaign found no basis for silently tightening the historical evidence to
open-market-only while the SEC code itself does not make that split.

## Ownership and owner scope

The raw filing can contain several owners and there is no primary owner.
Transactions belong to the filing, while owner participation is many-to-many.
Issuer-level totals must not multiply rows by owner count. Persons must be
identified by reporting-owner CIK, never name-only matching. Entity/trust and
10%-owner records remain separate and cannot be silently merged into natural
person officers/directors.

## Amendments

- Keep every accession append-only.
- The original filing may influence decisions from its own availability.
- An amendment may influence state only from its own availability.
- Never retrospectively repair an earlier decision.
- Link a Form 4/A to an original only when base form, issuer CIK, complete owner
  CIK set, original filing date, and structure yield one unique match.
- Quarantine zero/multiple matches.

## Duplicate identity

Within one accession, use accession plus the SEC table surrogate key/raw row
position. Across accessions, identical issuer, owner, date, shares, and price do
not prove a duplicate economic trade. For issuer aggregation, count each raw
transaction row once and keep owner participation separately. Amendment chains
are versions, not destructive replacements.

## Sources

- [Current Form 4 instructions](https://www.sec.gov/about/forms/form4data.pdf)
- [SEC bulk schema/code list](https://www.sec.gov/files/insider_transactions_readme.pdf)
- [Ownership XML technical specification](https://www.sec.gov/info/edgar/ownershipxmltechspec-v3.pdf)
