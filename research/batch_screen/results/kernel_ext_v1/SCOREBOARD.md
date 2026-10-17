# batch kernel_ext_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 317 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC36_N/B5_N` | **w20041119** | FAIL | reject_stage1 | - | -0.5584 | -0.6726 / 0.278 | -1.0793 / 0.159 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC41_R/A_N_R` | default | FAIL | reject_stage1 | - | -0.9891 | -0.0334 / 0.490 | -0.2893 / 0.442 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC41_R/B_N_R` | default | FAIL | reject_stage1 | - | -1.2076 | -0.1049 / 0.481 | -0.2058 / 0.459 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC41_R/C_N_R` | default | FAIL | reject_stage1 | - | -0.8848 | -0.0237 / 0.494 | -0.2380 / 0.454 | - | - | - | 0.9995 | rejected (stage 1) |
| `ib38_C/C` | default | FAIL | reject_stage1 | - | -0.3345 | -0.1217 / 0.312 | -0.0870 / 0.371 | - | - | - | 1.0000 | rejected (stage 1) |

