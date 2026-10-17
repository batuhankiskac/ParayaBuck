# batch gp_synth_d05 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 309 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gate_power_synth/syn_edge` | default | PASS | not_run_stage2 | - | +0.2898 | +0.2547 / 1.000 | +0.2669 / 1.000 | - | - | - | - | not declared (deflation) |
| `gate_power_synth/syn_edge_i` | default | PASS | not_run_stage2 | - | +0.2842 | +0.2453 / 1.000 | +0.2573 / 1.000 | - | - | - | - | not declared (deflation) |
| `gate_power_synth/syn_edge_p` | default | PASS | not_run_stage2 | - | +0.1084 | +0.0000 / 0.510 | +0.0000 / 0.513 | - | - | - | - | not declared (deflation) |
| `gate_power_synth/syn_null0` | default | PASS | not_run_stage2 | - | +0.2898 | +0.2547 / 1.000 | +0.2669 / 1.000 | - | - | - | - | not declared (deflation) |
| `gate_power_synth/syn_noise_p` | default | PASS | not_run_stage2 | - | +0.0000 | +0.0000 / 0.510 | +0.0000 / 0.513 | - | - | - | - | not declared (deflation) |
| `gate_power_synth/syn_noise_m` | default | PASS | not_run_stage2 | - | +0.0000 | +0.0000 / 0.510 | +0.0000 / 0.513 | - | - | - | - | not declared (deflation) |

