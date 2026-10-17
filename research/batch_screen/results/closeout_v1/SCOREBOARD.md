# batch closeout_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 316 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `IB35_K/C_K` | default | FAIL | reject_stage1 | - | -0.5742 | -0.1944 / 0.305 | -0.1218 / 0.355 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB24_2L/A_2L` | default | FAIL | reject_stage1 | - | -0.2463 | -0.4175 / 0.145 | -0.8229 / 0.019 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB24_2L/B_2L` | default | FAIL | reject_stage1 | - | -0.1129 | -0.3091 / 0.189 | -0.6699 / 0.031 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB24_2L/C_2L` | default | FAIL | reject_stage1 | - | -0.2533 | -0.7510 / 0.052 | -1.2373 / 0.003 | - | - | - | 1.0000 | rejected (stage 1) |

