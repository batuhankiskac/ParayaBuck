# batch mf_sleeve_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 324 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `MF1/S10` | default | FAIL | reject_stage1 | - | -0.4923 | +0.3597 / 0.619 | +0.0483 / 0.515 | - | - | - | 0.0000 | rejected (stage 1) |
| `MF1/S20` | default | FAIL | reject_stage1 | - | -1.2303 | +0.5335 / 0.591 | -0.1031 / 0.478 | - | - | - | 0.0000 | rejected (stage 1) |
| `MF1/S30` | default | FAIL | reject_stage1 | - | -1.9038 | +0.5846 / 0.577 | -0.3490 / 0.452 | - | - | - | 0.0025 | rejected (stage 1) |

