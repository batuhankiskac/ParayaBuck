# H06 offline core population — 2026-09-15

Status: `H06_OFFLINE_CORE_POPULATION_READY`.

This is a deterministic, offline structural population build. It performs no network acquisition, score, issuer notional aggregation, ranking, return, P&L, or backtest.

## Population

- Relevant accessions: 395,310
- Normalized submissions: 395,310
- Reporting-owner rows: 470,170
- Non-derivative source transactions: 1,043,985
- Transaction-owner observations: 1,357,116

## Lineage and owner semantics

- Resolved amendments: 519
- Unresolved amendments: 5,423; all affected future use fails closed.
- Joint-owner status: `JOINT_OWNER_TRANSACTION_EXPANSION_PROVEN`; expansion means jointly reported, not owner-specific actor attribution.
- Footnote status: `SEC_BULK_TRANSACTION_FOOTNOTE_LINKAGE_PROVEN`; only exact transaction field references are used.

## Decision and enrichment boundary

- Monthly decisions: 55
- Mapped core-pass rows: 85
- Cover targets: 13
- Raw SIP targets: 13
- Final identity, shares, and raw price remain pending and were not acquired.

## Determinism

- `normalized_relevant_transactions`: `21d6b17b7f62e437f9cf3d7d09ba056ca885e9af6d4f14ebf7964d3ca6d368d3` (run B `21d6b17b7f62e437f9cf3d7d09ba056ca885e9af6d4f14ebf7964d3ca6d368d3`)
- `lineage_versions`: `481d69a4dcf5e3fc5dbf88028af572c14a2f7b31b69b0f4a67b6416f0f84a8c8` (run B `481d69a4dcf5e3fc5dbf88028af572c14a2f7b31b69b0f4a67b6416f0f84a8c8`)
- `routine_history`: `9481a165f616c070f0208eadd70ae57231253d76cbef21350c3aef0c92077da5` (run B `9481a165f616c070f0208eadd70ae57231253d76cbef21350c3aef0c92077da5`)
- `owner_year_classification`: `2351916ba14538d52c8d48f2f3d131f5a10030ec58f110a5b82ec5275b7e28f0` (run B `2351916ba14538d52c8d48f2f3d131f5a10030ec58f110a5b82ec5275b7e28f0`)
- `signal_core_gates`: `442059d6bb335ed10c40dc0714387a7e82067deeed1688ca69e9b1e2476d138f` (run B `442059d6bb335ed10c40dc0714387a7e82067deeed1688ca69e9b1e2476d138f`)
- `monthly_decisions`: `e352ad2b9764102b1e88c4140dac824574ade40b42dc176a943856f97a65c611` (run B `e352ad2b9764102b1e88c4140dac824574ade40b42dc176a943856f97a65c611`)
- `transaction_decision_map`: `ecb75dd2d7f685974ca8566a281e611fa8111fe4beacb14e01b7341825a0574f` (run B `ecb75dd2d7f685974ca8566a281e611fa8111fe4beacb14e01b7341825a0574f`)
- `cover_enrichment_targets`: `2a54744e41ecb42c44971c850c42d39ea19fa20efca5393408ea13a43fac9c53` (run B `2a54744e41ecb42c44971c850c42d39ea19fa20efca5393408ea13a43fac9c53`)
- `raw_sip_targets`: `9410cd967156ccc38e3b08dc999f6f2709522d96fbe9bc906141a1cd8b2e1ef8` (run B `9410cd967156ccc38e3b08dc999f6f2709522d96fbe9bc906141a1cd8b2e1ef8`)

No H06 economic admission or promotion is claimed.
