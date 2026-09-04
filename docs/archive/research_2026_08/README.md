# Archived Research — 2026-08

**Archived at:** 2026-08-26  
**Head at archive:** `a69cbc36984e516da885c0aed3e2edaff0c68f61` (L_FINAL_PARITY_RESULTS)  
**Parent:** `84faf202947300663c900eb2cc05bfbf1529a314` (K_FINAL_PARITY_FREEZE)  
**Canonical dataset SHA (RETAINED in `data/historical/`):** `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`  
**Corporate actions SHA:** `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`  
**Archive reason:** Post-research cleanup — historical V3 research CLOSED, superseded experiments moved out of active `docs/`/`data/research/` to keep root focused on canonical state.

Root `docs/` now shows only current canonical state (15 active docs). This archive preserves reproducibility without appearing as current results.

> **Current index:** For present-day research-record status and retention, see
> the [current research index](../../research/README.md). This archive remains
> immutable historical evidence; its classifications are not current deletion
> authority.

---

## How to use this archive

All files here are **READ-ONLY evidence**. They are not imported by production code (`src/`, `config/settings.yaml`, `benchmarks/`). To reproduce a specific experiment, check out the commit SHA listed and run the runner named in `Relevant runner/script`.

Do not edit `FROZEN_V3_PARAMS` or `FROZEN_V3` values; they are locked.

---

## 1. Docs archived

| # | Document | Experiment | Conclusion | Canonical / Superseded | Relevant commit SHA | Notes |
|---|---|---|---|---|---|---|
| 1 | `survivorship_aware_dataset_2026_08.md` | Old survivorship demo: `equity_survivorship_aware_2016_2026_v2.csv` 1,937,021 bars, 1,418 symb (1,194 survivors + 224 sampled delisted from 300 inactive probe). Claims “full would be 2,863 inactive”. | Demo proves survivorship bias exists (≈+2–3pp CAGR inflated) but is **not entity-safe and not symmetric**. Superseded by v4 entity-safe (3,554) → v5 symmetric (9,010) | **SUPERSEDED** — retained for lineage | `93a0cd6` (COMMIT B/C/D audit) → superseded at `20bac77` (A_FULL) | `data/historical/equity_survivorship_aware_2016_2026_v2.*` + `data/research/survivorship_aware_2026_08/` also archived; manifest `survivorship_aware_dataset_2026_08.json` @ `7347` bytes |
| 2 | `v3_position_risk_optimization_2026_08.md` | Rejected position-risk research `v3_position_risk_2026_08` — candidates P1 `diversify_7`, P2 `diversify_8`, P3 `tail-ES126` vs control P0 `top8_max6 inv-vol 0.10`. 20 calendar folds, costs 1×/2×/3×/5×, 2,000 bootstraps, 14 promotion gates | **ALL REJECTED** — `promotion_verdict.json all_pass:false`. Gate5 maxDD worse by >2pp, gate11 literal fails; no production change, risk overlay unchanged (0.60 gross, 6 pos, 0.10 cap) | **REJECTED, SUPERSEDED** | `d404667` (research COMMIT B, rejected) manifest `v3_position_risk_optimization_2026_08.json` `bfb88a4` | `data/research/archive/2026_08/v3_position_risk_2026_08/` + `scripts/archive/research_2026_08/research_v3_position_risk.py` + `tests/test_v3_position_risk*` parity locks remain |
| 3 | `v3_exit_latency_optimization_2026_08.md` | Rejected exit-latency research `v3_exit_latency_2026_08` — candidates E0-E3 (daily top8/top12/hold-band/confirmed) vs weekly control. Same 20 folds, costs, bootstrap | **ALL REJECTED** — `promotion_verdict` no promotion; daily exits incur extra costs without improving Sharpe/Calmar; `axon_sanity` fails. No PAPER exit change (weekly remains) | **REJECTED** | `f6ae1bb` manifest `v3_exit_latency_optimization_2026_08.json` | `data/research/archive/2026_08/v3_exit_latency_2026_08/` + `scripts/archive/research_2026_08/research_v3_exit_latency.py` |
| 4 | `v3_risk_tail_loss_optimization_2026_08.md` | Rejected stop / tail-loss research `v3_risk_tail_loss_2026_08` — broker-native stops S1 fixed_8, S2 fixed_10, S3 ATR3, S4 ATR3_TRAIL + account halts R1 drawdown_5/7.5/10% via `ProductionPolicyEngine` | **ALL FAIL 12 gates** — `stop_candidates all_pass:false`, `worst loss <30%`, `maxDD <2pp`, `median fold`, `Sharpe wins`, `net stop value` all fail; `interaction_test SKIPPED`. Explicit DO NOT implement | **REJECTED** | `016afe8` manifest `v3_risk_tail_loss_optimization_2026_08.json` | `data/research/archive/2026_08/v3_risk_tail_loss_2026_08/` + `scripts/archive/research_2026_08/research_v3_risk_tail_loss.py` + `docs/research_helpers.py` |
| 5 | `equity_alpha_optimization_2026_08.md` | Phase-2 alpha optimization — predeclared small set vs CONTROL `+125.3% v2 vs v1`. Candidates `a3, h25/h50/h75, mh_raw/mh_std, sqrt-ivol, regime, hyst` (15 variants). `survivorship_bias_limitation:true`, 20 folds, no promotion decision | Research-only signal, no promotion, numbers biased by demo universe. Retained as negative-result evidence; MH_RAW later validated separately via `mh_raw_promotion` | **SUPERSEDED / RESEARCH-ONLY** | `3c5e57d` manifest `equity_alpha_optimization_2026_08.json` | `data/research/archive/2026_08/equity_alpha_optimization_2026_08/` + `scripts/archive/research_2026_08/research_equity_alpha_optimization.py` |
| 6 | `optimization_experiments_2026_08.md` | Evidence-only experiments 2026-08-21 — B1 cash yield +2.99pp at 3% uninvested, B3 sleeve 0.50→0.60, A1 crypto off (halve 0.05), B5 top_n 2→3, B6 shortlist 300→500, cash_parking, capital_foundation. No code change (`run_product_backtest` overrides) | No product change; tables remain evidence. Explicit “no code changed” per `validated_strategies.md` | **SUPERSEDED / EVIDENCE-ONLY** | — | `data/research/archive/2026_08/optimization_2026_08/`, `cash_parking_2026_08/`, `capital_foundation_2026_08/` etc; `docs/validated_strategies.md` is canonical spec |
| 7 | `release_validation.md` | Historical record 2026-08-10 for rejected `equity_dynamic_trend_1d_v1` (HEAD `7ae6645`, 2019-05-28..2026-07-30). “CURRENT” claims described THEN | Superceded by `capital_utilization_cadence_verdict.md` + `release_evidence_v2/` after trend family closed | **SUPERSEDED** | `7ae6645` → `rejected_strategies.md` | Keep `rejected_strategies.md` in root as frozen rejected-family index; this doc archived for audit |
| 8 | `strategy_diagnosis.md` | V1 root-cause attribution for `equity_dynamic_trend_1d_v1` (HEAD `3c05878`). Harness `scripts/strategy_diagnose.py` retired 2026-08-18 | Strategy family closed, numbers frozen, not comparable to v3 PIT | **SUPERSEDED** | `3c05878` | Also indexed via `rejected_strategies.md` |
| 9 | `strategy_validation_v2.md` | V2 validation `equity_long_horizon_momentum_1d_v2` (252/21/200 trend). Research evidence, harness retired | Superseded by residual momentum v1→v3; retained as historical comparison | **SUPERSEDED** | `8d9e...` | `data/research/strategy_validation_v2/` |
| 10 | `multi_market_strategy_validation.md` | Multi-market validation v2 — broad-pool alpha pipeline, TIMESTAMP-ALIGNED beta. Re-runs `equity_residual_momentum_v1` + `crypto_low_variance_v1`; v1 numbers RETRACTED | V1 retracted, v2 retained as comparison only | **SUPERSEDED** | — | `data/research/strategy_validation_v2/` |
| 11 | `crypto_reversal_validation.md` | `crypto_cross_sectional_reversal_v1` — NOT VALIDATED (family closed). Negative net economics, fails 2× costs, walk-forward coin flip | Closed, no smoke | **REJECTED** | `strategy_validation_v3/reversal.json` | `data/research/strategy_validation_v3/` |

---

## 2. Data/research archived (moved to `data/research/archive/2026_08/`)

| Experiment dir | Conclusion | Canonical? | Commit |
|---|---|---|---|
| `survivorship_aware_2026_08/` (15 JSON) | Demo 1,418 study, not symmetric | No — demo | `93a0cd6` |
| `v3_position_risk_2026_08/` (12 JSON) | P1-P3 all rejected | No | `d404667` |
| `v3_exit_latency_2026_08/` (12 JSON) | E0-E3 rejected | No | `f6ae1bb` |
| `v3_risk_tail_loss_2026_08/` (10 JSON) | S1-S4+R1-R4 fail | No | `016afe8` |
| `optimization_2026_08/` (26 files) + `cash_parking_2026_08/`, `capital_foundation_2026_08/`, `portfolio_construction_2026_08/`, `gates_2026_08/`, `regime_execution_2026_08/`, `risk_epoch_2026_08/`, `topn9_2026_08/` | Evidence-only diagnostics, no product change | No | `2026-08-21` batch |
| `equity_alpha_optimization_2026_08/` (22 JSON) | Phase-2 predeclared, no promotion | No | `3c5e57d` |
| `equity_hardening_2026_08/` (phase dirs: `baseline_corrected/` is canonical, `phase23_fixed/`/`phase0_baseline_head/` are smoke) | Hardening correctness record; smoke phases superseded | `baseline_corrected/` is canonical (retained via `docs/equity_strategy_hardening_2026_08.md`), rest superseded | `036abbb` |
| `exit_profit_protection_2026_08/` | Profit-protection stop variant rejected | No | — |

Full survivorship lineage: original `D_FULL` at `data/research_manifests/full_survivorship_reconstruction_2026_08.json` (`7d8f934`) is **SUPERSEDED** by the corrected chain `E_FULL→F→G→H` (see `data/research_manifests/full_survivorship_correction_*.json`). Duplicates `*_corrected.json` / `*_fixed.json` / `*_real.json` mirrors in `full_survivorship_2026_08/` have been deduplicated to single canonical `*_v5.json` (byte-identical). Synthetic `old_vs_full_walk_forward.json` + `selection_overlap.json` were invalid placeholders and have been deleted (real reruns `old_vs_full_walk_forward_real_v5.json` + `selection_overlap_real.json` are canonical). Build checkpoints `recovery_checkpoint.json` / `rejected_assets.json` etc were regenerable and deleted.

---

## 3. Scripts archived (`scripts/archive/research_2026_08/`)

All `research_*`, `strategy_*`, `audit_survivorship`, `build_*_dataset`, `corrected_v3_runner`, `run_corrected_v3` are **research-only** and are not imported by `src/`. Deleted `D` scripts (`run_v3_6combos_quick.py`, `run_full_v3_terminal*.py`, `build_full_resume_batched.py`, `materialize_full_bars.py`, `generate_full_reports.py`, `generate_missing_full_artifacts.py`, `regenerate_entity_safe.py`, `backfill_equity_history.py`) were temp/duplicate generators — git history retains them; they had 0 inbound imports.

---

## 4. Manifests

- `data/research_manifests/full_survivorship_correction_2026_08.json` (E_FULL `acc11e6`) — **CANONICAL** 6-correction predeclaration
- `full_survivorship_correction_F_2026_08.json` (`d272863`), `_G_2026_08.json` (`a6dbf8c`), `_H_2026_08.json` (`06e8d61`) — stages F/G/H
- `full_survivorship_reconstruction_2026_08.json` (`7d8f934`) — **SUPERSEDED** D_FULL, kept for lineage
- `v3_position_risk_optimization_2026_08.json`, `v3_exit_latency_optimization_2026_08.json`, `v3_risk_tail_loss_optimization_2026_08.json` — rejected predeclarations

---

## 5. What remains canonical in root

- `docs/strategy_architecture.md`, `docs/validated_strategies.md`, `docs/mh_raw_v3_paper_promotion_2026_08.md`, `docs/equity_strategy_hardening_2026_08.md`, `docs/optimization_tournament_2026_08.md`, `docs/crypto_bakeoff_validation.md`, `docs/capital_utilization_cadence_verdict.md`, `docs/dashboard.md`, `docs/strategy_robustness.md`, `docs/operations.md`, `docs/alerting.md`, `docs/product_foundation.md`, `docs/deployment.md`, `docs/product_audit.md`, `docs/paper_operations.md`, `docs/repository_cleanup_inventory_2026_08.md`, `docs/v3_historical_validation_final.md` (new)
- `data/historical/equity_pit_symmetric_2016_2026_v5.*` + `equity_survivorship_full_corporate_actions_v5.*` + `crypto_18_*_extended.csv` (canonical)
- `data/research/full_survivorship_2026_08/` — canonical v5 subset only (see `docs/v3_historical_validation_final.md`)
- `data/research_manifests/full_survivorship_correction_*.json` (E→H chain)
- `scripts/run_symmetric_v5_validation.py` + `scripts/build_symmetric_v5_pipeline.py` + `scripts/fetch_corporate_actions_v5.py` + `scripts/audit_symmetric_stage_a_coverage.py` + `scripts/research/run_v3_canonical_validation.py` (shim)
- `benchmarks/parity_guard.json`

---

*Archive index version 1.0 — 2026-08-26. For questions, see `docs/v3_historical_validation_final.md` for the final story and `docs/repository_cleanup_inventory_2026_08.md` for the full classification table.*
