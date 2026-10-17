# batch anchors_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 3 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `k_control/K` | control | control | - | +0.0000 | +0.0000 / 0.000 | +0.0000 / 0.000 | - | - | - | - | control |
| `r2_control/R2` | FAIL | reject_stage1 | - | -0.5933 | -0.2856 / 0.250 | -0.1311 / 0.360 | - | - | - | 1.0000 | rejected (stage 1) |
| `vol_shape_p15/p15` | PASS | reject_stage2 | G8, E1 | +0.4905 | +0.2507 / 0.635 | +0.3311 / 0.683 | +0.0246 | -0.1101 | PASS | 0.0000 | rejected (stage 2) |

