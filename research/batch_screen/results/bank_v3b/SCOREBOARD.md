# batch bank_v3b -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 303 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC59_K/A_K` | default | FAIL | reject_stage1 | - | -0.0443 | -0.0040 / 0.413 | -0.0013 / 0.472 | - | - | - | 0.9985 | rejected (stage 1) |
| `IC59_K/B_K` | default | FAIL | reject_stage1 | - | -0.0307 | -0.0020 / 0.434 | -0.0011 / 0.464 | - | - | - | 0.9935 | rejected (stage 1) |
| `IC59_K/C_K` | default | FAIL | reject_stage1 | - | -0.0423 | +0.0014 / 0.508 | -0.0009 / 0.497 | - | - | - | 0.7170 | rejected (stage 1) |

