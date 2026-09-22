# Activism classification specification

## Scope

An initial Schedule 13D is not automatically an activist campaign. The classifier is precision-first, deterministic, evidence-spanned, versioned, and fail-closed. It reads the filing available at the decision time; it never uses later amendments, campaign outcomes, press coverage, or returns.

## Required Item 4 representation

Preserve raw markup, normalized text, source document, offsets, and hash. Extract separate affirmative flags for current filer-attributed plans under Item 4 categories (a)–(j): securities transactions; extraordinary transaction; asset sale; board/management change; capitalization/dividend change; other material business/structure change; charter/bylaw impediments; delisting; deregistration; and similar actions.

Also record evidence-spanned labels for board nomination/proxy activity, issuer engagement, strategic alternatives, capital allocation, governance, investment-only purpose, reserved optionality, completed transactions, negations, mixed text, and unclear text.

## Deterministic rules

1. A positive plan requires affirmative, current, filer-attributed language and an exact evidence span.
2. Generic boilerplate reciting (a)–(j), “may consider,” “reserves the right,” and “no current plans” are not positive evidence.
3. `investment_only` requires explicit investment-purpose language, a valid scoped denial of affirmative plans, and no conflicting engagement evidence.
4. “Except as disclosed” requires the referenced disclosure to be resolved; otherwise classify `unclear`.
5. Conflicting affirmative and denial text is `mixed`.
6. Amendment text must be applied as a versioned operation (`restates`, `adds`, `deletes`, `no_change`, `unknown`); absence from a partial amendment is not deletion.
7. Any unresolved or unsupported case is excluded for manual audit. A language model may assist review but may not overwrite the deterministic label or raw evidence.

## Verdict

`ITEM4_ACTIVISM_CLASSIFICATION_FEASIBLE_ONLY_WITH_RAW_ACCESSION_CORPUS_AND_FAIL_CLOSED_REVIEW`

The current repository has neither corpus nor validated classifier. Index form type alone is insufficient.
