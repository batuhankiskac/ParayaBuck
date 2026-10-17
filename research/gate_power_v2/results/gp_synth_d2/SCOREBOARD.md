# batch gp_synth_d2 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 309 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gate_power_synth/syn_edge` | default | PASS | stage2_pass | - | +1.1967 | +1.0163 / 1.000 | +1.0738 / 1.000 | +1.4812 | +1.0765 | PASS | 0.0000 | not declared (deflation) |
| `gate_power_synth/syn_edge_i` | default | PASS | stage2_pass | - | +1.1835 | +0.9809 / 1.000 | +1.0373 / 1.000 | +1.4447 | +1.0383 | PASS | 0.0000 | not declared (deflation) |
| `gate_power_synth/syn_edge_p` | default | PASS | reject_stage2 | G5_base, AT_2017_01_to_end, B1_recent, B2_flat, FRESH | +0.4263 | +0.0000 / 0.510 | +0.0000 / 0.513 | +1.1966 | +0.4342 | FAIL | 0.1925 | rejected (stage 2) |
| `gate_power_synth/syn_null0` | default | PASS | stage2_pass | - | +1.1967 | +1.0163 / 1.000 | +1.0738 / 1.000 | +1.4812 | +1.0765 | PASS | 0.0000 | not declared (deflation) |
| `gate_power_synth/syn_noise_p` | default | PASS | reject_stage2 | G1_base, G5_base, G6_calibrated, AT_2010_01_to_end, AT_2017_01_to_end, B1_recent, B2_flat, E1, FRESH | +0.0000 | +0.0000 / 0.510 | +0.0000 / 0.513 | +0.0000 | -0.0000 | FAIL | 0.1925 | rejected (stage 2) |
| `gate_power_synth/syn_noise_m` | default | PASS | reject_stage2 | G1_base, G5_base, G6_calibrated, AT_2010_01_to_end, AT_2017_01_to_end, B1_recent, B2_flat, E1, FRESH | +0.0000 | +0.0000 / 0.510 | +0.0000 / 0.513 | +0.0000 | -0.0000 | FAIL | 0.1925 | rejected (stage 2) |

