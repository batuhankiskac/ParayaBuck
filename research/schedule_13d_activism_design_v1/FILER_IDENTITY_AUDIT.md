# Filer identity audit

## Separate identity domains

The data model must never collapse:

- subject issuer;
- EDGAR submitter/login or filing agent;
- each reporting person on a cover page;
- reporting group and membership interval;
- beneficial owner;
- activist organization or fund family.

The first ten digits of an accession identify the submitting entity and can belong to a third-party filing agent. Master-index CIK/name fields are discovery metadata and do not enumerate all reporting persons. Joint-filing agreements and exhibits may add people without CIKs.

## Resolution and campaign linkage

Names, addresses, jurisdictions, explicit CIKs, cover-page ordinals, and joint-filing evidence are stored as filed. Canonical identities are versioned and confidence-scored; an alias is not silently merged.

An initial is defined by exact form `SC 13D` or `SCHEDULE 13D`; an amendment by exact `/A` form. “Amendment No.” text does not replace the SEC form type. A first 13D can be a 13G-to-13D conversion and need not be a newly acquired position.

An amendment links first through an explicitly cited accession/date. Without one, linkage is allowed only when a unique prior candidate matches subject issuer CIK, covered-class fingerprint, and materially overlapping reporting-person roster. Link status is one of `explicit_parent`, `unique_deterministic_match`, `ambiguous`, or `orphan`; the last two are excluded from campaign inference.

## Verdict

`FILER_IDENTITY_RECONSTRUCTION_FEASIBLE_FAIL_CLOSED_BUT_NOT_PRESENT_LOCALLY`

No public filing field by itself proves that a filer belongs to a hedge-fund-activist universe. Ex-ante “activist reputation” would require a separately versioned, point-in-time evidence registry and is not a free ranking field.

## Filer-universe scope

No filer universe is frozen because the overall decision is REJECT. The broadest causal scope considered by this design is an external, non-passive initial 13D filer with affirmative Item 4 activism evidence, after excluding insiders/control holders and unresolved groups. The central empirical support is narrower and is dominated by hedge-fund-activist samples; it cannot be generalized automatically to every private 13D filer. A hedge-fund-only historical classifier is separately blocked until a versioned point-in-time organization/fund registry exists.
