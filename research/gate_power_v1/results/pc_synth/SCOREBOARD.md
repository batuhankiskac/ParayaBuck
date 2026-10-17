# batch pc_synth -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 306 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `synthetic_power/syn_edge_105` | default | PASS | reject_stage2 | G2_base, G2_calibrated, B1_recent, B2_flat, E1, FRESH | +0.2980 | +0.0031 / 0.503 | +0.0343 / 0.535 | +0.1621 | -0.0070 | FAIL | 0.0005 | rejected (stage 2) |
| `synthetic_power/syn_edge_110` | default | FAIL | reject_stage1 | - | +0.5787 | -0.0052 / 0.499 | -0.0029 / 0.498 | - | - | - | 0.0010 | rejected (stage 1) |
| `synthetic_power/syn_null` | default | FAIL | reject_stage1 | - | -2.4778 | -1.6884 / 0.054 | -0.6789 / 0.198 | - | - | - | 1.0000 | rejected (stage 1) |

