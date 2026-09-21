# Missing-data semantics

## Verdict

`SEC_MISSINGNESS_REQUIRES_FAIL_CLOSED_EXCLUSION`

The paper's Compustat missing-to-zero convention is not portable to SEC XBRL.
A missing fact may mean economic zero, aggregation, a custom tag, a different
standard concept, omitted separate disclosure, or parser/mapping failure.

Zero is accepted only when the correct concept and context contains an
explicit numeric zero. Absence from Company Facts, a calculation child list,
or a standard-tag registry is never zero evidence.

Every annual filing must be classified as one of:

- `COMPLETE_STANDARD`;
- `COMPLETE_VALIDATED_CUSTOM`;
- `PARTIAL_MISSING_COMPONENT`;
- `AMBIGUOUS_AGGREGATION`;
- `AMBIGUOUS_RD_SGA`;
- `INVALID_CONTEXT_OR_UNIT`;
- `DUPLICATE_CONFLICT`;
- `UNSUPPORTED_TAXONOMY`.

Only the first two are scoreable. Custom facts require a versioned semantic
registry based on exact definition, statement location and relationships—not
outcome-guided NLP.

In the 14-issuer discovery pilot, none of 190 Company-Facts-linked 10-K
accessions had every narrow standard-tag family with comparative instants.
`PrepaidExpenseCurrent` comparative coverage was zero across the pilot. This
does not prove CbOP is absent; it proves that a standard-tag-only substitution
or missing-zero rule is not faithful.
