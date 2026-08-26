# Data Research Archive — 2026-08

Created 2026-08-26 during post-research cleanup. Historical V3 research is FROZEN.

This directory is the on-disk counterpart to `docs/archive/research_2026_08/README.md`.
It holds the superseded JSON payloads moved out of `data/research/` so that the root
`data/research/` primarily shows CURRENT canonical state (`full_survivorship_2026_08/` v5 subset + `mh_raw_promotion_2026_08/`).

All files are read-only evidence. Do not edit; do not add to `docs/validated_strategies.md`.
Canonical SHAs remain pinned: `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` (v5 PIT) and `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` (CA v5).

## Contents

See `docs/archive/research_2026_08/README.md` for the full experiment → conclusion → canonical/superseded → commit SHA table.

Directories archived here:

- `survivorship_aware_2026_08/` — old demo 1,418 symb, superseded by v4 (3,554) → v5 (9,010) symmetric
- `v3_position_risk_2026_08/` — rejected P1-P3, no promotion
- `v3_exit_latency_2026_08/` — rejected E0-E3
- `v3_risk_tail_loss_2026_08/` — rejected S1-S4/R1-R4, interaction SKIPPED
- `optimization_2026_08/` + `cash_parking_2026_08/`, `capital_foundation_2026_08/`, `portfolio_construction_2026_08/`, `gates_2026_08/`, `regime_execution_2026_08/`, `risk_epoch_2026_08/`, `topn9_2026_08/` — evidence-only diagnostics
- `equity_alpha_optimization_2026_08/` — phase-2 predeclared, no promotion
- `equity_hardening_2026_08/` — smoke phases; `baseline_corrected/` is canonical and referenced by `docs/equity_strategy_hardening_2026_08.md`

Note on `full_survivorship_2026_08/` deduplication: duplicates `*_corrected.json` / `*_fixed.json` / `*_real.json` / synthetic `old_vs_full_walk_forward.json` and `selection_overlap.json` and build checkpoints (`recovery_checkpoint.json`, `rejected_assets.json`) have been removed from that directory, leaving only the single canonical `*_v5.json` set. See the docs archive README §2 for details.

Manifest lineage: `data/research_manifests/full_survivorship_correction_2026_08.json` (E_FULL) → `_F` → `_G` → `_H` is canonical; original `full_survivorship_reconstruction_2026_08.json` (D_FULL) is superseded but retained in `data/research_manifests/` for lineage.

