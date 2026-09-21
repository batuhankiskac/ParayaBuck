# Security mapping audit

Verdict: `INSIDER_SECURITY_MAPPING_BLOCKED`

## Required identity

An eligible event would require a contemporaneous join of issuer CIK,
accession, as-filed issuer trading symbol, reported security title, filing
acceptance time, transaction date, and one ParayaBuck entity lifetime. Today's
ticker is never sufficient.

## SEC side

Ownership XML requires issuer CIK and issuer trading symbol. It also reports
the security title at the transaction row. These are strong source-side keys,
but neither a ticker nor a free-text title proves that the traded security is
the historical ordinary share represented by a ParayaBuck bar.

## ParayaBuck side

The V5 lifetime table has effective symbol intervals and provider asset IDs,
but no CIK or security-class key. Only one populated rename-chain row is not
evidence of complete change/ticker-reuse coverage. The bars do not carry a
historical product taxonomy.

## Fail-closed policy

The following must be excluded until an effective-dated crosswalk exists:

- unmatched or multiply matched issuer CIK/symbol pairs;
- ticker reuse or unresolved rename intervals;
- preferred, convertible, warrant, right, unit, SPAC, derivative, trust, fund,
  ETP, or ADR-like classes not expressly covered by an approved policy;
- generic `Common Stock` titles when they cannot be tied to the listed class;
- any row whose reported security and ParayaBuck bar cannot be shown to be the
  same economic security.

SEC + the current lifetime file may later support a narrow fail-closed subset,
but that subset has not been built or coverage-audited. Material mapping
coverage is therefore unknown, not zero and not assumed.
