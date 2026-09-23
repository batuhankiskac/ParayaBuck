# Account-capacity migration V2 closure

Decision: `ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE`.

Migration V2 started from corrected production main
`8e48b301413a679e6d2b097c28e8711c50ab1a8b` and stopped before a frozen
specification or any six-versus-eight outcomes. The study did not run migration
economics. Production `maximum_positions` remains 6; strategy configuration and
PAPER/LIVE behavior were not changed.

The blocker was `SHARED_ACCOUNT_REPLAY_LIMITATION`: current product backtests
run one market per engine and do not demonstrate one evolving cash, position,
pending-order, fill, and gross ledger across equity and crypto. A later capacity
migration must start only after a research-only shared-account replay has
validated isolated incumbent parity and shared-account invariants.

The separate FILL-activity pagination finding was assessed as
`FILL_PAGINATION_IRRELEVANT_TO_MIGRATION_V2` under a flat, all-cash, no-pending
offline replay contract that imports no broker or production-ledger state. FILL
pagination was not the migration blocker.

The stop-state artifacts are preserved under annotated tag
`archive/research/account-capacity-migration-8-v2-inconclusive-2026-09`, which
targets `b78273c878c0cce21e28502b0f1a8a7b46208acd`. The local study branch is
being removed after this closure. No remote V2 study branch existed.

Future work must build and validate the shared-account replay from a new clean
main branch. Do not resume the V2 migration or infer cap-eight benefits from
isolated-market results.

## Shared-account replay V1 follow-up — 2026-09-23

The research-only replay V1 evidence is preserved at annotated tag
`archive/research/shared-account-replay-v1-blocked-2026-09`, pointing to
`1c1c21ef1631fd426b4e90188ac446db4eaa0fd2`. The tag was pushed and its
remote peeled target verified; the replay implementation was not merged.
Equity and crypto isolated parity passed, and the combined cap-six replay was
deterministic, but it recorded 21 timestamps with seven occupied/reserved
position slots under `maximum_positions = 6`. Its decision is
`SHARED_ACCOUNT_REPLAY_BLOCKED_BY_GENERIC_CORRECTNESS`; migration readiness is
`MIGRATION_REPLAY_NOT_READY`. No cap-eight arm or migration economics ran.
The replay observation did **not** establish the production root cause; a
separate production-correctness campaign must establish and repair it before
shared replay can be rebuilt and validated from corrected main.

## Shared-account replay V2 follow-up — 2026-09-23

The research-only V2 replay at `fb66be75aa77d48e78d586808c05ac40d617ca0d`
is preserved by the pushed annotated tag
`archive/research/shared-account-replay-v2-clock-blocked-2026-09`. Equity and
crypto isolated parity passed. Combined cap-six accounting passed with zero
over-cap timestamps, at most six actual positions and six occupied/reserved
claims; two runs produced the same event hash. No cap-eight arm or migration
economics ran. Its decision remains `SHARED_ACCOUNT_REPLAY_V2_BLOCKED` and
`MIGRATION_REPLAY_NOT_READY`: completed equity and crypto bars are labeled
before their full contents are physically available, so combined slot priority
under that label-time clock is unproven. The V2 implementation remains archived
research provenance, not validated replay infrastructure. A separate physical
availability clock study must repair and revalidate isolated semantics and the
combined cap-six replay before any capacity comparison.
