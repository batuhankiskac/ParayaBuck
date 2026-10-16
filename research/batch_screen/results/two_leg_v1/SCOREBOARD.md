# batch two_leg_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 115 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `ib25_2L/A_2L` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `ib25_2L/B_2L` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IB26_2L/A_2L` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IB26_2L/B_2L` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IB26_2L/C_2L` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `ib27_2L/A_2L` | FAIL | reject_stage1 | - | -1.3164 | -1.7169 / 0.005 | -0.8650 / 0.030 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib27_2L/B_2L` | FAIL | reject_stage1 | - | -1.3305 | -1.9682 / 0.001 | -1.1056 / 0.010 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib27_2L/C_2L` | FAIL | reject_stage1 | - | -1.3833 | -1.7873 / 0.002 | -1.0467 / 0.007 | - | - | - | 1.0000 | rejected (stage 1) |

