# Downstream dependency classification

The old crypto baseline's fills, state, NAV and some stateful targets changed. This campaign does not rerun downstream studies.

| Artifact or gate | Classification | Reason and next use |
| --- | --- | --- |
| `archive/research/crypto-exit-giveback-audit-v2-2026-09` and earlier position-exit giveback audit | `STALE_REQUIRES_RERUN` | Position episodes, entry/exit prices and costs are baseline-dependent. Redo against the causal control before using exit economics. |
| `archive/research/shared-account-replay-v1-blocked-2026-09` | `SUPERSEDED_PROVENANCE_ONLY` | Its generic correctness block remains a historical diagnostic; no usable combined replay is established. |
| `archive/research/shared-account-replay-v2-clock-blocked-2026-09` | `SUPERSEDED_PROVENANCE_ONLY` | Its physical-clock/canonical-source block remains evidence. Restart with fresh equity and corrected crypto parity. |
| `archive/research/physical-availability-clock-v1-canonical-stop-2026-09` | `SUPERSEDED_PROVENANCE_ONLY` | The timing counterexample is valid; prior canonical crypto comparator is replaced. |
| `docs/research/account_capacity_migration_v1_closure.md` and `account_capacity_migration_v2_closure.md` | `UNAFFECTED` for their blocked/inconclusive conclusions | They do not authorize a cap change. Any new quantitative migration prerequisite must use a newly verified shared-account replay. |
| `archive/research/account-capacity-migration-8-v1-inconclusive-2026-09` and v2 counterpart | `SUPERSEDED_PROVENANCE_ONLY` for economics | Existing inconclusive decisions stand, but old crypto economics cannot support new cap-8 conclusions. |
| `docs/research/canonical_v3_baseline.md` and total-account research comparisons using v2 crypto control | `STALE_REQUIRES_RERUN` where crypto cashflows/slots enter results | Rebuild only in a separate fresh shared-account campaign after physical-clock parity. |
| Production PAPER/LIVE strategy, risk and broker path | `UNAFFECTED` | Historical proxy only; no production code or configuration changed. |

The handoff sequence is equity physical-clock parity, corrected crypto parity, combined cap-6 replay, deterministic independent account verification, then a separate decision on whether to reopen cap-8 migration. No migration is started here.
