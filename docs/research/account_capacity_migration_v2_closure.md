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
