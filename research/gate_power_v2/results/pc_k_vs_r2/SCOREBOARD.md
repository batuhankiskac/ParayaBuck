# batch pc_k_vs_r2 -- SCOREBOARD

control: `r2_control/R2` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 303 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `k_control/K` | default | PASS | reject_stage2 | G5_base, G5_calibrated | +0.5933 | +0.2856 / 0.750 | +0.1311 / 0.640 | +0.0653 | +0.1575 | PASS | 0.0000 | rejected (stage 2) |

