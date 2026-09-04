# Repository Cleanup Inventory — 2026-08-26

**HEAD at freeze:** `a69cbc36984e516da885c0aed3e2edaff0c68f61` (L_FINAL_PARITY_RESULTS) + `84faf20` (K_FINAL_PARITY_FREEZE)  
**Canonical dataset:** `data/historical/equity_pit_symmetric_2016_2026_v5.csv` SHA `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`  
**Corporate actions:** `data/historical/equity_survivorship_full_corporate_actions_v5.csv` SHA `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`  
**Parity guard:** `benchmarks/parity_guard.json` (combined hash `93fd28374f3b48ac4d1684952bd80fe1c7f1b69dd9d688f7b4d0858bbe4dd10c`)  
**Tests at freeze:** 745 passed, ruff clean, mypy clean 72 files  
**Scan date:** 2026-08-26

> This document classifies every relevant file before any deletion/move. All deletions/moves below preserve canonical PIT SHAs and frozen V3 math (`formation 252 / skip 21 / beta 273 / residual_vol 63 / top_n 8 / ivol 0.70 / max_single 0.10 / weekly`).
> Produced before COMMIT A per mission §2.

> **Historical proposal — not current retention authority.** This inventory is
> the 2026-08-26 proposal frozen at the commits above. Subsequent changes mean
> its path and retention claims may no longer match the current tree. See the
> [current research index](research/README.md) for the current lifecycle. Do
> not treat this historical classification as automatic deletion authority.

---

## 1. Classification Key

| Category | Meaning | Action |
|---|---|---|
| **A PRODUCTION_REQUIRED** | Runtime for PAPER/LIVE: strategy, execution, scanner, config, monitoring, tests guarding live invariants | KEEP, no archive |
| **B CANONICAL_RESEARCH_REQUIRED** | Minimal code/artifacts to reproduce final symmetric PIT result (SYMMETRIC_FULL_T1) | KEEP, documented canonical entry point |
| **C ARCHIVAL_RESEARCH** | Superseded experiments still useful historically but not active development | MOVE to `docs/archive/research_2026_08/` or `data/research/archive/2026_08/` with index |
| **D DEAD/REDUNDANT** | Duplicates, synthetic placeholders, temp generators, stale checkpoints, placeholder artifacts | DELETE (git history retains) |

---

## 2. Production-Required (A) — Keep

### 2.1 Source — `src/parayabuck/` (72 files)

| Path | Category | Reason | References / Imports | Replacement |
|---|---|---|---|---|
| `src/parayabuck/config.py` | A | Single config loader, validates `equity_residual_momentum_v3` + LIVE gate | imported by `runtime.py`, `cli.py`, `tests/test_config.py` | — |
| `src/parayabuck/runtime.py` | A | PAPER/LIVE engine, warmup, signal-basis latch, reconciliation | imports `portfolio/runtime_service`, `universe/*`, `execution/*` | — |
| `src/parayabuck/portfolio/equity_residual_momentum_v3.py` | A | PAPER strategy v3 (MH_RAW frozen: positive 12-1 gate → 6/9/12 rank consensus → ivol 0.70 → top8 → inv-vol 0.10) | `portfolio_strategy_from`, `tests/test_equity_residual_momentum_v3.py`, `config/settings.yaml strategy.equity.name` | — |
| `src/parayabuck/portfolio/equity_residual_momentum.py` | A | Base for v2/v3 (weekly, V2 API) | imported by v3 | — |
| `src/parayabuck/portfolio/crypto_low_variance.py` | A | Crypto v2 (96h) PAPER sleeve | `config/settings.yaml strategy.crypto` | — |
| `src/parayabuck/portfolio/runtime_service.py` | A | `PortfolioRuntime` + `PortfolioDecisionService` share same risk/planner for backtest/paper/live | `runtime.py`, `tests/test_portfolio_runtime.py` | — |
| `src/parayabuck/universe/scanner.py` | A | Equity scanner (20d median dollar vol, ATR, completeness) — production PIT discovery | `runtime.py:ensure_active_universes`, `backtest/universe.py` | — |
| `src/parayabuck/universe/crypto_scanner.py` | A | Crypto scanner | `runtime.py` | — |
| `src/parayabuck/universe/preflight.py` | A | Universe refresh, 150-batch Alpaca, spread/BTC-corr gates | `scan_equity_universe.py` | — |
| `src/parayabuck/backtest/portfolio_engine.py` | A | Historical broker, accounting, fee schedule | `backtest/product.py`, `tests/test_portfolio_engine_accounting.py` | — |
| `src/parayabuck/backtest/product.py` | A | `run_product_backtest`, `build_product_universe`, `hash_point_in_time_universe` — production backtest surface | `scripts/run_symmetric_v5_validation.py`, `benchmarks/parity.py` | — |
| `src/parayabuck/backtest/universe.py` | A | `PointInTimeUniverse.contains` bisect, trailing PIT window | `backtest/product.py`, `runtime.py` | — |
| `src/parayabuck/backtest/portfolio_report.py` | A | Metrics, Sharpe/Sortino/maxDD | `backtest/product.py` | — |
| `src/parayabuck/backtest/terminal_aware_engine.py` | **B*** | *Research-only historical delisting model — see §4. No production importer (`grep src/` = 0 hits outside self). Keep in place but marked RESEARCH-ONLY; not exported from `backtest/__init__.py`. | imported only by `scripts/run_symmetric_v5_validation.py`, `tests/test_terminal_aware_engine.py`, `scripts/run_corrected_v3.py` | remain, annotated |
| `src/parayabuck/backtest/terminal_policy.py` | **B*** | *Research-only terminal classification (T0-T3) — leaf, no production importer | imported only by terminal_aware_engine + 2 survivorship tests | remain, annotated |
| `src/parayabuck/execution/*` | A | Alpaca execution, ledger, telemetry | `runtime.py`, `reconcile_paper_incident.py` | — |
| `src/parayabuck/web/server.py` | A | Loopback dashboard, healthz/readyz, strategy cards | `runtime.py`, `tests/test_web_*.py` | — |
| `src/parayabuck/data/*` | A | Alpaca fetch, buffers `BarStore`, cache, adjustment `merge_provider_authority` | `runtime.py`, `universe/*` | — |
| `src/parayabuck/monitoring/*` | A | Metrics, alerts, paper_incident | `runtime.py` | — |
| `src/parayabuck/markets/*` | A | Symbols canonicalization, sectors, timeframes | `portfolio/*`, `universe/*` | — |
| `src/parayabuck/core/models.py` | A | `Bar`, `Market` primitives | all | — |
| **All other `src/parayabuck/**/*`** (logger, storage, backup, live_safety, runtime_state, validation) | A | Production runtime/validation surfaces, all imported by at least one test or `runtime.py` | grep confirms active imports | — |

\* Terminal modules are B (canonical research) but live under `src/parayabuck/backtest/` for packaging simplicity; §4 explains why not moved to `research/`.

### 2.2 Config & Runtime Data

| Path | Category | Reason | References | Replacement |
|---|---|---|---|---|
| `config/settings.yaml` | A | PAPER config: `strategy.equity.name=equity_residual_momentum_v3`, `maximum_allocation 0.60`, `live.enabled false`, scanner gates, risk caps | `load_config`, `tests/test_strategy_spec_freeze.py` | — |
| `config/experiments/*` | A/B | Experiment presets (if any) — retain if referenced, else C | grep shows minimal refs | keep |
| `data/historical/equity_pit_symmetric_2016_2026_v5.csv` + `.manifest.json` | **B** | Canonical symmetric PIT dataset 9010 symb 11.6M bars SHA bb7aaa... | `run_symmetric_v5_validation.py` DATASET_V5, `data/research/full_survivorship_2026_08/*v5*` | — |
| `data/historical/equity_entity_lifetimes_full_v5.csv` | **B** | Lifetimes for v5 (6,858 active + 2,152 inactive) SHA ed675d9... | `run_symmetric_v5_validation.py` LIFETIMES_V5 | — |
| `data/historical/equity_survivorship_full_corporate_actions_v5.csv` + `.manifest.json` | **B** | CA v5 141,908 events (dividends 141,908 + splits/mergers/spinoffs) SHA a0a60... | `run_symmetric_v5_validation.py` CA_V5 | — |
| `data/historical/crypto_18_1d_extended.csv` + `crypto_18_1h_extended.csv` | A | Canonical crypto datasets (22k 1d, 538k 1h) | `benchmarks/parity.py`, crypto v2 papers | — |
| `data/runtime/parayabuck.sqlite3` | A | Production ledger (decisions, fills, ownership, risk state) | `runtime.py:Storage`, `backup.py` | — |
| `data/runtime/active_equity_universe.json` + `active_crypto_universe.json` | A | Active PIT universes (freshness, scanner digest) | `runtime:ensure_active_universes` | — |
| `data/universe/equity_candidates.json` | A/B | Candidate pool cache 35.9 MB (regenerable but expensive) — retain as cache | `preflight.py` | — |
| `reports/robustness/{equity,crypto,combined}.json` | A | Canonical robustness pack (neighborhood/rolling/regime/jackknife) | `docs/strategy_robustness.md`, `scripts/strategy_robustness.py` | — |
| `benchmarks/*.py` + `benchmarks/parity_guard.json` | A/B | Parity harness + runtime baseline (post-research) | mission §8, §11 | — |

### 2.3 Scripts — Production (A)

| Path | Category | Reason | References / Imports | Replacement |
|---|---|---|---|---|
| `scripts/scan_equity_universe.py` | A | Runtime equity scanner entry `refresh_equity_universe` | `docs/strategy_architecture.md`, `src/universe/preflight` | — |
| `scripts/run_crypto_universe_scan.py` | A | Runtime crypto scan | `docs/deployment.md` | — |
| `scripts/audit_v3_paper_soak.py` | A | PAPER soak audit (RO SQLite), HEAD 199d153 → closed | `docs/mh_raw_v3_paper_promotion_2026_08.md:66` (`python3 scripts/audit_v3_paper_soak.py --pretty`), `tests/test_v3_paper_audit.py` | — |
| `scripts/reconcile_paper_incident.py` | A | Cash vs fill reconciliation after halt | `docs/operations.md:32`, `tests/test_paper_incident.py` | — |
| `scripts/execution_quality_report.py` | A | Ledger cost calibration (configured vs observed slippage) | `docs/paper_operations.md:96`, `parayabuck.execution.telemetry` | — |
| `scripts/paper_smoke.py` | A | Gated PAPER lifecycle smoke (AAPL 1 share, paper=True, mode paper, ALLOW_PAPER_SMOKE=YES, ENABLE_LIVE≠YES) | `docs/paper_operations.md`, `docs/capital_utilization_cadence_verdict.md`, gated by env | — |
| `scripts/run_paper_chaos.py` | A | Offline fake-broker chaos gate `pytest -m paper_chaos` 90 scenarios | `README:135`, `docs/product_audit.md:126 PASS` | — |

### 2.4 Docs — Production (A) — 15 files

`docs/strategy_architecture.md`, `docs/mh_raw_v3_paper_promotion_2026_08.md`, `docs/validated_strategies.md`, `docs/equity_strategy_hardening_2026_08.md`, `docs/optimization_tournament_2026_08.md`, `docs/crypto_bakeoff_validation.md`, `docs/capital_utilization_cadence_verdict.md`, `docs/dashboard.md`, `docs/strategy_robustness.md`, `docs/operations.md`, `docs/alerting.md`, `docs/product_foundation.md`, `docs/deployment.md`, `docs/product_audit.md`, `docs/paper_operations.md` — all current product contracts/promotion receipts/operational runbooks, referenced by `config/settings.yaml`, `PRODUCT_STRATEGY_NAMES`, `tests/test_strategy_spec_freeze.py`. **All KEEP.**

---

## 3. Canonical Research-Required (B) — Keep

### 3.1 Canonical Runner & Builders

| Path | Category | Reason | References / Imports | Replacement |
|---|---|---|---|---|
| `scripts/run_symmetric_v5_validation.py` | **B** | **FINAL K_FINAL_PARITY_FREEZE runner** (816 lines). Production PIT parity: `build_product_universe` → Stage-A 300 → V3 ranking → risk overlay → `TerminalAwareResearchEngine(policy, CA v5)` with `universe_version=hash_point_in_time_universe`. Frozen `FROZEN_V3_PARAMS 252/21/273/63/8/0.10/0.70 weekly`, emits `walk-forward 20-fold` + `parity_canary OLD_T0 vs FULL_T1` + `audit_universe_for_engine`. SHA chain H→K→L. Supersedes all prior runners. | imports `build_product_universe`, `hash_point_in_time_universe`, `TerminalAwareResearchEngine/TerminalInfo`, `load_config`, `portfolio_strategy_from`, `rebalance_policy_for`; referenced by `data/research_manifests/full_survivorship_*` as successor | **CANONICAL — KEEP as `scripts/run_symmetric_v5_validation.py`** (documented in §3; optional shim `scripts/research/run_v3_canonical_validation.py` re-exports) |
| `scripts/build_symmetric_v5_pipeline.py` | B | Builds canonical dataset `equity_pit_symmetric_2016_2026_v5.csv` + lifetimes + conflict report. Exhaustive active+inactive × NASDAQ/NYSE/AMEX/ARCA/BATS, SIP split, provider UUID identity, VERIFIED_TICKER_REUSE exclusion | reads Alpaca `/v2/assets` + `/v2/stocks/bars`, referenced by runner | KEEP |
| `scripts/fetch_corporate_actions_v5.py` | B | Builds CA v5 143k rows via paginated `GET /v1/corporate-actions` for all 9010 symbols. CA parity complement | referenced by runner | KEEP |
| `scripts/audit_symmetric_stage_a_coverage.py` | B | Proves symmetric population: exact PIT Stage-A weekly simulation 492 Mondays 2017–2026, measures `active_outside_old` entry rate, yearly breadth | reads v5 CSV, emits `symmetric_v5_coverage_report.json` | KEEP |

### 3.2 Data & Manifests — Canonical Chain (B/A)

| Path | Category | Reason | References | Replacement |
|---|---|---|---|---|
| `data/research_manifests/full_survivorship_correction_2026_08.json` (E_FULL) | B | Predeclaration 6 corrections (terminal lifecycle, real walk-forward, entity-safe ticker reuse, CA parity, selection overlap, symmetry audit). Parent of F/G/H chain | commit `acc11e6` | KEEP |
| `data/research_manifests/full_survivorship_correction_F_2026_08.json` | B | Stage F — corrected dataset identity (v5) | SHA linkage | KEEP |
| `data/research_manifests/full_survivorship_correction_G_2026_08.json` | B | Stage G — runner freeze `corrected_v3_v5_symmetric_1.0` T0–T3 | script `corrected_v3_runner.py` | KEEP |
| `data/research_manifests/full_survivorship_correction_H_2026_08.json` | B | Stage H — corrected 6 path-dependent results (production-PIT) | supersedes `full_survivorship_reconstruction_2026_08.json` (D_FULL) | KEEP |
| `data/research/full_survivorship_2026_08/full_v3_results_v5.json` | B | Canonical T0_T1_T2_T3 results (OLD_T0 + SYMMETRIC_FULL_T0/T1/T2/T3) — SYMMETRIC_FULL_T1 = +113.6% / CAGR 7.44% / Sharpe 0.639 / maxDD 22.7% | parity_canary hash | KEEP |
| `data/research/full_survivorship_2026_08/parity_canary.json` + `parity_manifest.json` | B | Terminal lifecycle parity canary (quantity==0, no stale value, no future SELL) + old/new universe_hash + effective_params | mission §1 parity guard | KEEP |
| `data/research/full_survivorship_2026_08/final_verdict_v5.json` | B | Verdict `SYMMETRIC PIT RECONSTRUCTION VALIDATED — V3 DATA ROBUSTNESS STRONG` | canonical_baseline SYMMETRIC_FULL_T1 | KEEP |
| `data/research/full_survivorship_2026_08/old_vs_full_walk_forward_real_v5.json` | B | Real 20-fold independent walk-forward (calendar 6-month folds, independent engine, PIT universe, corrected CA) | E_FULL §2 correction | KEEP |
| `data/research/full_survivorship_2026_08/selection_overlap_real.json` | B | Real weekly V3 selection overlap (Jaccard, weight deltas) | E_FULL §5 | KEEP |
| `data/research/full_survivorship_2026_08/terminal_event_accounting_audit_v5.json` | B | Per-entity held-at-termination audit + proceeds | mission §1 | KEEP |
| `data/research/full_survivorship_2026_08/*_v5 audit mirrors` (`active_symmetry_audit_v5.json`, `corporate_actions_audit_v5.json`, `ticker_identity_conflicts_v5.json`, `universe_audit_v5.json`, `symmetric_v5_coverage_report.json`) | B | Canonical v5 diagnostics — proving broad active pool, CA pagination, reuse, Stage-A breadth | — | KEEP |
| `data/research/full_survivorship_2026_08/terminal_sensitivity_v5.json` | B | Terminal policy sensitivity T0–T3 bounds | mission T2/T3 | KEEP |
| `benchmarks/parity_guard.json` | B | Parity guard capturing head + SHAs + PAPER parity + SYMMETRIC_FULL_T1 snapshot | — | KEEP |
| `docs/v3_historical_validation_final.md` | B | Canonical final validation doc (§6) — to be created | — | CREATE |

### 3.3 Historical lineage data (B) — retain for audit (not canonical runtime)

| Path | Category | Reason | References | Replacement |
|---|---|---|---|---|
| `data/historical/equity_survivorship_aware_full_2016_2026_v4.csv` + `.manifest.json` + `equity_entity_lifetimes_full_v4.csv` + `equity_survivorship_full_corporate_actions_v4.csv` + `.manifest.json` | B | Superseded v4 entity-safe asymmetric 3,554 symb SHA `763fd85...` — last pre-symmetric validated as F_FULL/H correction baseline | `data/research_manifests/full_survivorship_reconstruction_2026_08.json` (D_FULL) → E_FULL supersession | RETAIN (archive, not canonical runtime) |
| `data/historical/equity_survivorship_aware_full_2016_2026_v3.csv` + `.manifest.json` + `equity_entity_lifetimes_full_v3.csv` | B | Superseded v3 ticker-stitching (pre-entity-safe) | C→D diff audit | RETAIN minimally |
| `data/historical/equity_1200_2016_2026.csv` + `equity_1200_dividends.csv` | B | Biased baseline 1,194 symb SHA `3baec12...` — CONTROL in old manifests | `bars_sha` pinned in 14 research manifests | RETAIN for reproducibility |
| `data/research_manifests/full_survivorship_reconstruction_2026_08.json` | B | Original D_FULL reconstruction manifest — SUPERSEDED per E_FULL, keep for lineage | — | RETAIN |

---

## 4. Research-Only Modules — Namespace Decision

| Path | Category | Reason | References / Imports | Replacement |
|---|---|---|---|---|
| `src/parayabuck/backtest/terminal_aware_engine.py` (15.9KB, `TerminalAwareResearchEngine`) | **B** | Research-only path-dependent terminal lifecycle engine (E_FULL freeze). Touches private engine state `_positions/_cash/_pending/_equity` for T1/T2/T3. No production importer (`grep src/` = 0 hits outside `terminal_policy` leaf). Not exported from `backtest/__init__.py`. | imported by `run_symmetric_v5_validation.py`, `scripts/run_corrected_v3.py`, `tests/test_terminal_aware_engine.py` | **RETAIN IN PLACE** — annotate header `RESEARCH-ONLY: historical delisting model, not a runtime feature`. Move to `research/backtest/` would require packaging `research/` + import rewrites with zero benefit; proven no production dependency (see `grep` above). |
| `src/parayabuck/backtest/terminal_policy.py` (12.3KB) | **B** | Leaf classification `TerminalClassification`, `compute_terminal_proceeds`, `should_apply_haircut`, `apply_terminal_policy_to_result`. Imported only by engine + 2 survivorship tests | — | Same — retain, annotated |

*Verification for §4:* `grep -R terminal_aware_engine|terminal_policy src/` → only the two self-files + `terminal_policy` internal import. `grep src/parayabuck/runtime.py|portfolio/|universe/|execution/|web/|config.py` → 0 hits. No circular research imports. Blast radius none to production install.

---

## 5. Archival Research (C) — Move to Archive with Index

Move to `docs/archive/research_2026_08/` (markdown evidence) and `data/research/archive/2026_08/` (large JSON), indexed by `docs/archive/research_2026_08/README.md`. Root `docs/` and `data/research/` will show only current canonical state after. All retain `commit SHA` proximity for reproducibility; no SHA deletion.

### 5.1 Docs — Superseded / Rejected Research (C) — 8 files → `docs/archive/research_2026_08/`

| Path | Category | Reason | References | Replacement |
|---|---|---|---|---|
| `docs/survivorship_aware_dataset_2026_08.md` | C | Old survivorship demo artifact: v2 1,937,021 bars 1,418 symb (1194+224), explicitly “300 inactive sample; full would be 2863”. Superseded by v4 (3554) → v5 (9010). | `data/historical/equity_survivorship_aware_2016_2026_v2.*`, `data/research/survivorship_aware_2026_08/` | `docs/v3_historical_validation_final.md` |
| `docs/v3_position_risk_optimization_2026_08.md` | C | Rejected P1 diversify_7, P2 diversify_8, P3 tail-ES126 all **REJECTED** (gate5 maxDD, gate11 literal; verdict unchanged; no prod change). | `data/research/v3_position_risk_2026_08/promotion_verdict.json` (all_pass:false), manifest `bfb88a4` | archived, no prod change |
| `docs/v3_exit_latency_optimization_2026_08.md` | C | Rejected E0-E3 (exit delay) research, no production exits | manifest `f6ae1bb` | archived |
| `docs/v3_risk_tail_loss_optimization_2026_08.md` | C | Rejected S1 fixed_8, S2 fixed_10, S3 ATR3, S4 ATR3_TRAIL + R1-R4 all fail 12 gates, `interaction_test SKIPPED`, DO NOT implement | manifest `016afe8` | archived |
| `docs/mh_raw_promotion_validation_2026_08.md` | C→B | Pre-promotion MH_RAW gate COMMIT C, superseded by `mh_raw_v3_paper_promotion_2026_08.md` receipt but retained for reproducibility | `data/research/mh_raw_promotion_2026_08/` | archive, keep receipt in root |
| `docs/rejected_strategies.md` | B | Frozen archive of rejected families (retain but clarify not active) — kept in root as frozen archive, also indexed in archive README | `docs/strategy_diagnosis.md` | keep root + index |
| `docs/equity_alpha_optimization_2026_08.md` | C | Phase-2 predeclared small set, RESEARCH-ONLY `survivorship_bias_limitation:true`, no promotion | `equity_alpha_optimization_2026_08.json` | archived |
| `docs/release_validation.md` | C | Historical 2026-08-10 record for rejected `equity_dynamic_trend_1d_v1` (HEAD 7ae6645), superseded by `capital_utilization_cadence_verdict.md` + `release_evidence_v2/` | `docs/rejected_strategies.md` | archived |
| `docs/optimization_experiments_2026_08.md` | C | Evidence-only experiments B1(+2.99pp cash yield), B3(sleeve 0.50→0.60), A1(crypto off) — no code change, table evidence. Archive as experiment log | `data/research/optimization_2026_08/` | archived |
| `docs/strategy_diagnosis.md`, `docs/strategy_validation_v2.md`, `docs/multi_market_strategy_validation.md`, `docs/crypto_reversal_validation.md` | C | Retraction/diagnosis records for v1-v2 pipelines (V1 root causes, v2 long-horizon, multi-market RETRACTED, reversal NOT VALIDATED) — retain but archive clarity | `docs/rejected_strategies.md` | archive subset |

*Note: `docs/rejected_strategies.md` stays in root as canonical rejected-family index; archive README will reference it.*

### 5.2 Data — Superseded Research Payloads (C) → `data/research/archive/2026_08/`

| Path | Category | Reason | References | Replacement |
|---|---|---|---|---|
| `data/research/survivorship_aware_2026_08/` (15 JSON) | C | Old demo V2 1418 artifacts (entity_lifetimes, inactive_asset_probe, old_vs_new_walk_forward demo) | `docs/survivorship_aware_dataset` | archive |
| `data/research/v3_position_risk_2026_08/` (12 JSON: P1-P3, bootstrap, jackknife, walk_forward) | C | Rejected position-risk all no promotion | `tests/test_v3_position_risk_integrity.py` | archive |
| `data/research/v3_exit_latency_2026_08/` (12 JSON: E0-E3) | C | Rejected exit-latency | `tests/test_v3_exit_latency_integrity.py` | archive |
| `data/research/v3_risk_tail_loss_2026_08/` (10 JSON: S1-S4, R1-R4, interaction SKIPPED) | C | Rejected stop/tail | — | archive |
| `data/research/optimization_2026_08/` (26 files) | C | Walkforward `wf_eq_topn9.json` etc evidence-only (contains real engine runs but tabled in `optimization_experiments`) | `docs/optimization_experiments` | archive |
| `data/research/exit_profit_protection_2026_08/` | C | Exit profit protection rejected variant (intrabar_sensitivity, walk_forward 342KB) | — | archive |
| `data/research/equity_alpha_optimization_2026_08/` (22 JSON) | C/B | Phase-2 predeclared no promotion, `survivorship_bias_limitation:true` | — | archive |
| `data/research/equity_hardening_2026_08/*` excluding `baseline_corrected/` | C | Hardening intermediate smoke runs (`phase23_fixed/`, `phase0_baseline_head/`) | — | archive (keep `baseline_corrected/` as A) |
| `data/research/topn9_2026_08/`, `portfolio_construction_2026_08/`, `gates_2026_08/`, `regime_execution_2026_08/`, `cash_parking_2026_08/`, `capital_foundation_2026_08/`, `risk_epoch_2026_08/`, `equity_etf_foundation_1d_v1/`, `strategy_validation_v1/`, `strategy_validation_v2/`, `strategy_validation_v3/` | C/B | Diagnostic archives (B for valid historical evidence, C for rejected/eclipsed) | see table in §2 Data Inventory | archive |
| `data/research/full_survivorship_2026_08/` — non-canonical mirrors (see §7) | **D/C** | Duplicates to deduplicate (see §7) | — | deduplicate, keep canonical v5 subset |
| `data/research/mh_raw_promotion_2026_08/`, `mh_raw_shadow_2026_08/` | B | Validated promotion gate + shadow log — keep or archive as B | — | retain (optionally keep in place) |

### 5.3 Scripts — Research-Only (C) → `scripts/archive/research_2026_08/` or deleted per §5 choice

| Path | Category | Reason | References | Replacement |
|---|---|---|---|---|
| `scripts/research_v3_exit_latency.py` (74.5KB) | C | E0-E3 exit delay research, 1.6k lines, no PAPER change | `data/research/v3_exit_latency_2026_08/`, `tests/test_v3_exit_latency_integrity.py` | archive |
| `scripts/research_v3_position_risk.py` (79.6KB, 1891 lines) | C | P0-P3+ES126, independent engines, RO | `tests/test_v3_position_risk*` | archive |
| `scripts/research_v3_risk_tail_loss.py` (75.5KB) | C | S0-S4/R0-R4, gap= open, conservative | — | archive |
| `scripts/research_robustness_audit.py` (64.5KB) | C | Cadence robustness audit | `docs/capital_utilization_cadence_verdict.md` | archive |
| `scripts/research_crypto_cadence.py` (53.5KB) | C | Crypto cadence 22d blocks | — | archive |
| `scripts/research_equity_cadence.py` (55.3KB) | C | Equity cadence | — | archive |
| `scripts/research_deployment_start.py` (18.5KB) | C | Deployment-start staleness | — | archive |
| `scripts/research_equity_staleness.py` (7.2KB) | C | Staleness diagnostic | — | archive |
| `scripts/research_equity_hardening.py` (25.2KB) | C | Hardening variants `control,a1,a2,a3,c_tr,topn6/9` | `docs/equity_strategy_hardening` | archive |
| `scripts/research_production_policy.py` (12.1KB, `ProductionPolicyEngine`) | C | Production policy P0-P3 research engine | — | archive |
| `scripts/research_helpers.py` | C | Shared helpers (`config_digest`, `dataset_identity`, `_cost_stress`, `_fold_stats`) used by 8 research scripts, not runtime | `research_equity_*`, `research_crypto_cadence`, `research_mh_raw_*` | archive with bundle |
| `scripts/research_mh_raw_validation.py` (48.5KB) | C | MH_RAW promotion validation `control,mh_raw` | `data/research/mh_raw_promotion_2026_08/` | archive |
| `scripts/research_mh_raw_shadow.py` (10.5KB) | C | MH_RAW shadow replay | — | archive |
| `scripts/research_equity_alpha_optimization.py` (51KB, 15 variants `a3,h25,h50,mh_raw`) | C | Equity alpha optimization 15 candidates vs CONTROL +125.3% | `data/research/equity_alpha_optimization_2026_08/` | archive |
| `scripts/strategy_robustness.py` (45.8KB) | C | Frozen robustness pack (local neighborhood [240/252/264], rolling 252/126, regime, jackknife, bootstrap) — produces `reports/robustness/{equity,crypto,combined}.json` | `docs/strategy_robustness.md` | archive (keep report canonical) |
| `scripts/combined_portfolio_validation.py` (23.7KB) | C | 50% equity+50% crypto validation | `docs/strategy_robustness.md` | archive |
| `scripts/strategy_foundation.py` (35KB) | C | 9-ETF foundation download/evaluate | `docs/optimization_experiments_2026_08.md:210` | archive |
| `scripts/run_v3_survivorship_comparison.py` | C | Frozen OLD 1194 vs NEW v2 1418 early demo (20 folds + survivor cohort) — superseded by v4/v5 | `docs/survivorship_aware_dataset_2026_08.md` | archive |
| `scripts/audit_survivorship.py` (12.5KB) | C | Initial audit 1,193/1,194 survivors, asset master | `docs/survivorship_aware_dataset_2026_08.md §1` | archive |
| `scripts/build_survivorship_dataset.py` | C | Builds v2 demo 1,418 (300 sample) | `data/historical/equity_survivorship_aware_2016_2026_v2.csv` | archive |
| `scripts/build_full_survivorship_dataset.py` (22.6KB) | C | Builds v3 full 3,554 (SHA 763fd85...), filter NASDAQ/NYSE/AMEX/ARCA/BATS | `RESEARCH_DIR/recovery_checkpoint.json` | archive (retain manifest) |
| `scripts/fetch_corporate_actions_v4.py` (10.7KB) | C | CA v4 28k rows for 3,554 universe | H chain prior | archive |
| `scripts/corrected_v3_runner.py` (4.5KB) | C/B | G_FULL freeze predeclaration `RUNNER_IDENTITY {runner_version:corrected_v3_v5_symmetric_1.0, policies T0–T3, frozen:true}` — declares invariants, no results execution | `data/research_manifests/full_survivorship_correction_G_2026_08.json: script=scripts/corrected_v3_runner.py` | archive (record) |
| `scripts/run_corrected_v3.py` (11.1KB) | C | H_FULL v4 corrected 6 path-dependent runs OLD_T0/DEMO_T0/FULL_T0–T3 | `data/research_manifests/full_survivorship_correction_H` | archive |
| `scripts/run_v3_6combos_quick.py` | **D** | Duplicate H_FULL quick — superseded, no docs/manifest refs | grep finds no inbound imports | **DELETE** (see §7) |
| `scripts/run_full_v3_terminal.py` (452 lines) | **D** | Post-hoc `apply_terminal_policy_to_result` (non-path-dependent), stale `liquidate_at_end=False` notional delta — superseded by `TerminalAwareResearchEngine` | — | **DELETE** |
| `scripts/run_full_v3_terminal_v2.py` (400 lines) | **D** | Resume duplicate of above | — | **DELETE** |
| `scripts/build_full_resume_batched.py` (22.6KB) | **D** | Temp batched builder `bars_by_aid_cache.json` — duplicates `build_symmetric_v5_pipeline.py` 6 workers 80-batch | — | **DELETE** |
| `scripts/materialize_full_bars.py` (18.1KB) | **D** | Temp materialization `bars_materialized_checkpoint.json` → v3 CSV | — | **DELETE** |
| `scripts/generate_full_reports.py` (16KB, 393 lines) | **D** | Temp synthetic report generator superseded by `audit_symmetric_stage_a_coverage.py` exact Stage-A | — | **DELETE** |
| `scripts/generate_missing_full_artifacts.py` (11.1KB) | **D** | Stale placeholder — synthesises via `random.gauss`, `TICKER_REUSE_i` ×14, estimated cohort PnL `110k/-23k`, synthetic `final_verdict` method:estimated — violates E_FULL real-engine requirement (`synthetic placeholder derived from control`) | no `src` | **DELETE** |
| `scripts/regenerate_entity_safe.py` (10.1KB, 213 lines) | **D** | Entity-safe v3→v4 regen 27 VERIFIED_TICKER_REUSE — subsumed by `build_symmetric_v5_pipeline.py` 9,010 handling | — | **DELETE** |
| `scripts/backfill_equity_history.py` (5.8KB) | **D** | IEX backfill 2016→2019 to `equity_1200_2016_2019.csv` — pre-survivorship, not PIT | — | **DELETE** |

*Archival choice:* C scripts are moved to `scripts/archive/research_2026_08/` (preserve SHA history) unless explicitly flagged D-deleted (9 files). D files are deleted (git history retains).

---

## 6. Tests — Classification

| Module | Category | Action | Reason | References |
|---|---|---|---|---|
| 49 modules: `test_portfolio_runtime.py`, `test_hardening.py`, `test_signal_adjustment_parity.py`, `test_runtime_universe_refresh.py`, `test_auto_start_retry.py`, `test_portfolio_engine_accounting.py`, `test_product_backtest.py`, `test_safety_incidents.py`, `test_restart_chaos.py`, `test_crypto_low_variance.py`, `test_explanation_metadata.py`, `test_crypto_scanner.py`, `test_execution_telemetry.py`, etc. (605 tests) | **A PRODUCTION_INVARIANT** | KEEP | Guard live idempotence, ledger, costs, safety, web, scanner, strategy correctness | `src/parayabuck/{runtime,portfolio,execution,web,data,markets}` |
| `test_v3_position_risk_integrity.py` (19), `test_v3_exit_latency_integrity.py` (9), `test_v3_position_risk_cost_stress_integrity.py` (5), `test_full_survivorship_terminal.py` (11), `test_terminal_aware_engine.py` (5), `test_v3_paper_integration.py` (5), `test_equity_residual_momentum_v3.py` (12), `test_pit_universe_parity.py` (2), `test_validation_contract.py` (12), `test_v3_paper_audit.py` (1) + `test_universe_snapshots.py*` + `test_strategy_spec_freeze.py*` (109 tests total final V3 parity) | **B FINAL_V3_PARITY** | KEEP, FROZEN | Lock V3 math, survivorship dataset correctness, promotion gates, CA parity; `bars_sha 3baec12...` and `config_digest` pinned | `scripts/research_v3_*`, `data/research_manifests/*`, `src/portfolio/equity_residual_momentum_v3.py` |
| `test_research_alpha.py` (19), `test_indicators.py` (2), `tests/helpers.py` (0) | **C OBSOLETE_RESEARCH_RUNNER** | ARCHIVE/REMOVE per §12 | Research-only blend/horizon/regime/hysteresis regressions now frozen as V3; parity covered by `test_equity_residual_momentum_v3`. Stub 294B no contract. `helpers.py` keep if paper_chaos still uses (it does — 8 tests via `apply_entry_fill`) | `src/portfolio/research_alpha.py`, `scripts/research_equity_alpha_optimization.py` | Archive `test_research_alpha.py` + `test_indicators.py` with their scripts; retain `helpers.py` (move to `tests/fixtures/` if desired, but keep for now) |

*Detailed test counts: 745 total at freeze; 605 production + 109 parity + 19 research_alpha + 2 indicators + remaining helpers accounted. See scout `ClassifyTestsImports` for per-module breakdown.*

---

## 7. Temporary Artifact Noise — Deduplicate / Delete (D/C)

### 7.1 Full survivorship `data/research/full_survivorship_2026_08/` duplicates

| Path Pattern | Action | Reason | References / SHA check | Replacement |
|---|---|---|---|---|
| `full_v3_results.json` + `full_v3_results_corrected.json` vs **`full_v3_results_v5.json` (CANONICAL)** | Deduplicate → keep `full_v3_results_v5.json` (H_FULL), delete/archive others after `sha256sum`+`diff` proves v5 canonical supersedes | Evolution H → H_fixed → v5 parity (same engine, v5 CA 141,908). `full_v3_results.json` is pre-H placeholder | verify `cat parity_manifest.json` → v5 universe_hash `c6f8344...` | canonical `full_v3_results_v5.json` |
| `corrected_v3_results_H.json` vs **`corrected_v3_results_H_fixed.json` (keep fixed)** | Keep `_fixed` (latest, 3.5KB adds 1.4KB H_FIXED accounting fix), archive base as C | 2 variants, fixed supersedes base | `data/research_manifests/full_survivorship_correction_H` | `_fixed` |
| `old_vs_full_walk_forward.json` (synthetic 10.2KB, method derived from control) | **DELETE** (D synthetic) | Invalid per `full_survivorship_correction_2026_08.json §2`: “synthetic placeholder derived from control, no independent engine” | superseded by `old_vs_full_walk_forward_real.json` (real 20 folds) | `old_vs_full_walk_forward_real_v5.json` |
| `old_vs_full_walk_forward_real.json` vs **`old_vs_full_walk_forward_real_v5.json` (dup)** | Keep one (byte-identical 10.9KB), archive other as C | Duplicate pair, v5 suffix is canonical H_FULL naming | `sha256sum` both | one canonical `_real_v5` |
| `selection_overlap.json` (CSV-universe overlap, 794B, not decision-level weekly) | **DELETE** (D synthetic attribution) | Per E_FULL §5: invalid, superseded by `selection_overlap_real.json` (weekly Jaccard + weight deltas, 2.2KB) | — | `selection_overlap_real.json` |
| `final_verdict.json` + `final_verdict_corrected.json` + **`final_verdict_v5.json` (CANONICAL)** | Keep `final_verdict_v5.json` (1.2KB, `SYMMETRIC PIT VALIDATED`), delete/archive others after SHA verify (`final_verdict.json` dup of v5 per 1.2KB) | Evolution: pre-v5 verdict → corrected → v5 final `canonical_baseline SYMMETRIC_FULL_T1` | — | `final_verdict_v5.json` |
| `old_demo_full_comparison.json` + `old_demo_full_comparison_corrected.json` | Keep one (C duplicates, 3.2KB intermediate correction), archive other | Derived aggregation fix COMMIT C | — | single canonical (compare with `full_v3_results_v5` authoritative) |
| `terminal_sensitivity.json` + `terminal_sensitivity_corrected.json` + **`terminal_sensitivity_v5.json` (CANONICAL)** | Keep `terminal_sensitivity_v5.json`, archive others | Intermediate corrected step superseded by v5 5.9KB | — | `terminal_sensitivity_v5.json` |
| `terminal_event_accounting_audit.json` vs **`terminal_event_accounting_audit_v5.json`** | Keep `v5` (per-entity held-at-termination audit, production-PIT) | Duplicate audit pair, v5 is H_FULL parity | — | `v5` |
| `final_verdict.json` dup noted above | — | — | — | — |
| `active_symmetry_audit_v4.json`, `terminal_event_classification_v4.json`, `corporate_actions_audit_v4.json`, `ticker_identity_conflicts_v4.json` | B archive — V4 diagnostic audits, keep for lineage but not canonical runtime | — | — |
| `recovery_checkpoint.json` (298KB), `rejected_assets.json` (892KB), `full_asset_discovery.json` (811B), `materialize_progress.json` | **D** delete-safe — build checkpoint / rejected-asset scratch, regenerable | discovery pagination, no provenance |  | delete after gate |

### 7.2 Other artifact noise (D/C)

| Path | Category | Reason | References | Replacement |
|---|---|---|---|---|
| `data/historical/equity_1200_2016_2019.json` (527B registry stub) | D | Derived, regenerable via `equity_1200_dividends.csv`, not PIT | — | delete-safe |
| `data/research/archive candidates` already listed §5.2 | C | — | — | — |
| `data/cache/warmup.sqlite3` (4.2GB) | D | Regenerable via `PortfolioRuntime.seed()` + `AlpacaHistoricalData.get_bars_batch`; gitignored, safe to purge (expensive rebuild) — retain cache by default, but classified D for purge-safe | `runtime.py:WarmupCache` | keep cache until operator opts purge |
| `data/cache/v5_bars/` (empty shard dir) | D | Empty, regenerable | — | delete-safe |
| `data/smoke/bench_*.sqlite3`, `benchmarks_local/` | D | Smoke/bench scaffolds regenerable via `benchmarks/` | `benchmarks/bench_backtest.py` | gitignored, delete-safe |
| `data/storage.sqlite` (32KB legacy) | D | Superseded by `parayabuck.sqlite3` 2.2MB | grep migration | delete-safe after check |
| `reports/research/` (67+ hash dirs `v2_spec`, `v2_signal_*`, `v2_wf_m3_f*`), `reports/equity_*_oos/train/` (hash dirs `57eeb...`) | D | Gitignored derived backtest curves/weight histories regenerable via `run_product_backtest` + `research_helpers`; no SHA pinned, not referenced by canonical tables | `docs/validated_strategies.md` | delete-safe (retain `reports/robustness/` canonical) |
| `data/research_manifests/full_survivorship_reconstruction_2026_08.json` (D_FULL) | B | SUPERSEDED per E_FULL, keep for lineage | — | retain as archive marker |
| `data/research_manifests/survivorship_aware_dataset_2026_08.json` etc | C | Demo predeclaration superseded | — | archive |
| `*_corrected.json` / `*_fixed.json` / `*_real.json` suite noted above | C/D | Deduplicate to single canonical per §5 manifest (keep v5) | — | — |

---

## 8. Dependencies / Import Audit Summary

| Area | Finding | Evidence | Action |
|---|---|---|---|
| Production → terminal modules | **NONE** — 0 production importers | `grep -R terminal_aware_engine|terminal_policy src/` → only self-files | No move needed; annotate RESEARCH-ONLY |
| Scripts → private internals | **NONE** violating (1 legit subclass override `research_v3_position_risk.py:91 _equity_fee`) | `grep scripts/ "_Position|_adverse|_pending|_equity_fee"` → only that override + string `modeled_adverse_bps` | No fix |
| Circular research imports | **NONE** | helper one-way `research_helpers → product/run_product_backtest`, no back-edge | — |
| Unused `src/` modules | **NONE** — all src files under `backtest/`, `portfolio/`, `execution/`, `config/`, `web/`, `runtime/` imported by ≥1 test or runtime entry | codegraph explore pre-filtered, grep confirm | — |
| Unused scripts | **9 D scripts** identified ( §5.3 D ) with 0 inbound imports, superseded by `build_symmetric_v5_pipeline.py` / `run_symmetric_v5_validation.py` | grep `from parayabuck|import` inbound = 0 | **DELETE** §7 |
| Unused tests | `test_indicators.py` (2 stub, 294B) placeholder, likely no contract; `test_research_alpha.py` (19) superseded by V3 freeze, parity covered by `test_equity_residual_momentum_v3` | `tests/test_cleanup.py` `offenders==[]` for legacy names; helpers used by 8 paper_chaos tests | Archive research_alpha+indicators with scripts; keep helpers |
| Reach for `src/parayabuck/backtest/terminal_*` in docs | Documented as research-only historical delisting model (§4) | — | Annotate headers |

---

## 9. Archive Index Skeleton (to be created at `docs/archive/research_2026_08/README.md` + `data/research/archive/2026_08/README.md`)

Each archived item will carry: `experiment | conclusion | canonical/superseded | relevant commit SHA`. See mission §5 for required fields. Skeleton generated during COMMIT A and populated after moves.

*Canonical vs archived root rule:* After COMMIT A, root `docs/` primarily shows **current canonical state** (15 A docs); root `data/research/` will show only `full_survivorship_2026_08/` canonical v5 subset + active validation `mh_raw_promotion_2026_08/`; `data/historical/` highlights v5 pair with v2/v3/v4 noted as lineage in manifest. All superseded moved under `archive/2026_08/` per §§5–7.

---

## 10. Planned Artifact Counts

| Action | Count | Examples |
|---|---|---|
| **DELETE** | 9 scripts + 7 data/research full_survivorship duplicates + ~15 D-cache/report artifacts + 2 placeholder manifests | `run_v3_6combos_quick.py`, `run_full_v3_terminal*.py`, `build_full_resume_batched.py`, `materialize_full_bars.py`, `generate_full_reports.py`, `generate_missing_full_artifacts.py`, `regenerate_entity_safe.py`, `backfill_equity_history.py`; `old_vs_full_walk_forward.json`, `selection_overlap.json`, `recovery_checkpoint.json`, `rejected_assets.json`, `data/storage.sqlite`, `reports/research/` |
| **ARCHIVE** | ~24 scripts + 15 docs fragments + ~15 research data dirs + 2 placeholder JSON families | `research_v3_*`, `research_crypto_cadence.py`, `audit_survivorship.py`, `build_*_dataset.py`, `docs/v3_position_risk*`, `data/research/{v3_position_risk,v3_exit_latency,v3_risk_tail_loss,optimization_2026_08,survivorship_aware_2026_08,...}` |
| **KEEP** | 7 production scripts + 4 canonical builders + `src/*` 72 files + ~28 canonical data artifacts + 15 canonical docs | `run_symmetric_v5_validation.py`, `build_symmetric_v5_pipeline.py`, `fetch_corporate_actions_v5.py`, `audit_symmetric_stage_a_coverage.py`, `parayabuck.sqlite3`, `benchmarks/parity_guard.json` |
| **ANNOTATE** | 2 research-only modules retained in place | `terminal_aware_engine.py`, `terminal_policy.py` → header banner RESEARCH-ONLY |

---

## 11. Verification

- **Producer parity verified before any deletion/move:** `benchmarks/parity_guard.json` combined `93fd2837...` matches head `a69cbc3` equity `4ff734...` / crypto `004735...`; `full_v3_results_v5.json` SYMMETRIC_FULL_T1 `+113.6%` / `cagr 0.07442` / `sharpe 0.639` / `maxDD 0.227` matches mission reference; `sha256 bb7aaa...` / `a0a607...` manifests present.
- **No production import of research engines:** `grep src/` = 0 (§4), therefore retaining `terminal_aware_engine.py` annotated is safe and avoids churn.
- **Archive completeness:** Every C/D path listed above has `category, reason, references/imports, replacement` per §2; deletions gated on SHA checks and no inbound `src/tests/docs` imports (grep confirmed for D set).
- **Post-move production install:** `pip install -e .` will not expose historical experimental engines as runtime features beyond the annotated source leaf (not exported).

---

*Inventory prepared per mission §§2–13, before deletions/moves. Next: COMMIT A (inventory/archive cleanup) → COMMIT B (code cleanup) → COMMIT C (measured optimizations) → COMMIT D (docs/final parity).*
