# batch opts_fix_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 303 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC56/A` | default | FAIL | reject_stage1 | - | -0.1825 | -0.1396 / 0.000 | -0.1472 / 0.000 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC56/B` | default | FAIL | reject_stage1 | - | -0.1400 | -0.1049 / 0.001 | -0.1107 / 0.002 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC56/C` | default | FAIL | reject_stage1 | - | -0.1825 | -0.1396 / 0.000 | -0.1472 / 0.000 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC40/A` | default | FAIL | reject_stage1 | - | -1.3388 | -0.8091 / 0.425 | -1.4203 / 0.353 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC40/B` | default | FAIL | reject_stage1 | - | -1.3388 | -0.8091 / 0.425 | -1.4203 / 0.353 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC40/C` | default | FAIL | reject_stage1 | - | -2.8096 | -0.9617 / 0.453 | -0.9547 / 0.468 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC31/A` | **w20020731** | FAIL | reject_stage1 | - | -0.2287 | +0.9594 / 0.537 | +2.8532 / 0.594 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC31/B` | **w20020731** | FAIL | reject_stage1 | - | +0.2809 | +nan / 0.618 | +5.3494 / 0.678 | - | - | - | - | rejected (stage 1) |
| `IC31/C` | **w20020731** | FAIL | reject_stage1 | - | -0.5326 | +nan / 0.004 | -32.1796 / 0.004 | - | - | - | - | rejected (stage 1) |
| `IC35/A` | **w20060523** | FAIL | reject_stage1 | - | +1.8302 | +nan / 0.543 | +4.6480 / 0.596 | - | - | - | - | rejected (stage 1) |
| `IC35/B` | **w20060523** | FAIL | reject_stage1 | - | +1.5329 | +13.8156 / 0.933 | +15.3250 / 0.945 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC35/C` | **w20060523** | FAIL | reject_stage1 | - | -0.2802 | +nan / 0.006 | -34.4287 / 0.006 | - | - | - | - | rejected (stage 1) |

