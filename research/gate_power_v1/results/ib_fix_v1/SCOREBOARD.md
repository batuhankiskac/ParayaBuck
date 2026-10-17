# batch ib_fix_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 107 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `IB12/A` | FAIL | reject_stage1 | - | -2.1283 | -1.8479 / 0.343 | -1.3991 / 0.382 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB12/B` | FAIL | reject_stage1 | - | -2.1598 | -1.0137 / 0.363 | -0.6664 / 0.417 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB12/C` | FAIL | reject_stage1 | - | -0.9907 | -0.3621 / 0.407 | -0.3378 / 0.418 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB14/A` | FAIL | reject_stage1 | - | -0.2887 | -2.3055 / 0.325 | -1.7322 / 0.369 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB14/B` | FAIL | reject_stage1 | - | -0.8332 | -1.1454 / 0.345 | -0.7359 / 0.400 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB14/C` | FAIL | reject_stage1 | - | -0.3224 | -0.4401 / 0.432 | -0.3380 / 0.445 | - | - | - | 1.0000 | rejected (stage 1) |

