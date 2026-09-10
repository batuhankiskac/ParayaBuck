# H06 local Form 4 normalization — 2026-09-09

This is a separate research-only normalization package over receipt-bound SEC Form 4 and Form 4/A bodies already present locally. It performs no network access, economic run, PAPER/LIVE action, or security-continuity inference.

The package validates `3993` body hashes and form-specific HTML content. It parses `10314` typed Table I/II rows and retains `274` pre-taxonomy candidate purchase rows for a future gate; economic admission remains zero.

Disposition counts: `{"AMENDMENT_FORM_4A": 208, "CANDIDATE_OPEN_MARKET_PURCHASE": 274, "EXCLUDED_DERIVATIVE_SECURITY": 2472, "EXCLUDED_FROZEN_TAXONOMY": 4773, "EXCLUDED_NON_PURCHASE_DIRECTION": 965, "NON_TRANSACTION_HOLDING_ROW": 1574, "UNKNOWN_CODE_OR_DIRECTION": 48}`.

Amendment lineage: `26` uniquely linked of `62` Form 4/A documents; `5` documents contain both linked and unmatched rows, while `36` are entirely unmatched. Row-level unmatched/non-unique evidence is `149` references across `41` unique accessions, retained with explicit status.

The frozen taxonomy excludes Form 4/A, derivative rows, grants, conversions, issuer dispositions, gifts, tax withholding and option exercises; open-market purchases use code P with acquired direction A. Other codes remain explicitly unknown or excluded by direction. Routine history is descriptive only because no horizon or threshold is invented.

H06 remains blocked for security/ticker continuity, complete historical coverage, and a separately frozen routine rule. This package must not be treated as economic or candidate approval.
