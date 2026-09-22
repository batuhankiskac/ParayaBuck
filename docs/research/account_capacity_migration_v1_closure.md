# Account-capacity migration v1 closure

Decision: `ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE`.

The stopped study branch is `research/account-capacity-migration-8-v1` at
`ede3b3dfadfd5f3f0bdef040a14d20365c8df469`, created from production main
`478b15bd7c56d75735de0d94f456a2b9b9b491e8`. The frozen six-versus-eight
specification is at `3f7d0bc2b443f66b95d67ccb0503f1a0567799f5` and predates
the stop. The final report and decision JSON confirm that neither arm ran and
no economic output was generated. The branch contains study documents only.

The study stopped when it found that broker-wide `OPEN` order queries could
silently omit orders after Alpaca's first response page. That generic
correctness issue must be repaired independently before any account-capacity
comparison. The archive tag
`archive/research/account-capacity-migration-8-v1-inconclusive-2026-09`
preserves the stopped branch as provenance.

The v1 specification is no longer executable authority. Do not resume its
branch or run either arm from its frozen protocol. Any future migration must
start from corrected production main and freeze its protocol again before
outcomes. Production `maximum_positions` remains unchanged by this stopped
study.
