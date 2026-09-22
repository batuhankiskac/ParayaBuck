# Current capability audit

## Repository state

The audit started from clean `main` at `754f4060b1eb38593201b2a269e9593d083eb534`; that commit matched `origin/main`. Work is confined to `research/schedule_13d_activism_design_v1/` on branch `research/schedule-13d-activism-design-v1`.

## Reusable capabilities

- The canonical equity history and effective-dated lifetime files are present and their hashes match `docs/research/canonical_v3_baseline.md`.
- The backtest engine already enforces completed-bar decisions, next-tradable-bar-open fills, sell-before-buy ordering, and terminal-lifecycle handling.
- The account ledger has an account-wide six-position maximum. PAPER is configured for 0.55 equity, 0.05 crypto, 0.60 gross, 0.40 cash; LIVE is disabled with a zero cap.
- The archived insider-source reassessment supplies a useful pattern for accession provenance and effective-dated fail-closed mapping. Its Form 4 fields are not reusable as 13D semantics.

## Missing capabilities

- There is no historical Schedule 13D accession corpus, downloader, normalized schema, Item 4 classifier, reporting-group resolver, amendment linker, or issuer-class-to-security crosswalk.
- Existing entity lifetimes contain provider/entity/symbol history, but no CIK, CUSIP, or covered security class.
- The scanner's broad `US_EQUITY` population is not a point-in-time common-stock taxonomy.
- The portfolio cadence supports monthly, weekly, and `hours:N`; it does not natively schedule an event from EDGAR acceptance to the first eligible regular-session open.
- The incidental submissions JSON cache is only a small schema fixture. It lacks the accession documents required for historical classification.

## Capability verdict

`CURRENT_REPOSITORY_NOT_READY_FOR_13D_ECONOMICS`

These gaps would require a research-only acquisition/parser/mapping layer. They are not the primary decision reason here: the design is rejected earlier because the literature does not establish a defensible unconditional post-publication long leg.
