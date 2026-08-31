# V3 Historical Validation — Final (Frozen)

**Status:** `HISTORICAL V3 RESEARCH FROZEN — FINAL`  
**Frozen at:** 2026-08-26, HEAD `a69cbc36984e516da885c0aed3e2edaff0c68f61` (L_FINAL_PARITY_RESULTS)  
**Parent freeze:** `84faf202947300663c900eb2cc05bfbf1529a314` (K_FINAL_PARITY_FREEZE)  
**PAPER strategy:** `equity_residual_momentum_v3` (MH_RAW) — promoted, no LIVE  
**LIVE:** `false` (`live.enabled false`, `hard_dollar_cap 0`, `PARAYABUCK_ENABLE_LIVE` gate closed)  
**Next phase:** PAPER forward validation; no further V3 historical optimization

> This is the single authoritative historical narrative. All prior intermediate numbers (OLD_T0 survivor-biased, FULL_T0/T2/T3 bounds, rejected stop/position-risk tables) are not current results and appear only as sensitivity context below.

> **Additive operational-parity erratum (2026-08-31):** the receipt frozen at `a69cbc3...` remains an immutable historical record of its 0.60-allocation, 2026-07-31 run. The later runtime-sized observation-context implementation produced a separate current-policy comparator (`0.55` allocation, through `2026-07-30`, `+76.4538%` total, Sharpe `0.571342`) during the 2026-08 research cycle. That comparator is recorded in `docs/equity_v3_alpha_execution_2026_08.md`; it is not a replacement or a claim of performance decay for this frozen receipt.

---

## 1. V3 Identity (Frozen)

| Field | Value |
|---|---|
| Strategy name | `equity_residual_momentum_v3` |
| Class | `src/parayabuck/portfolio/equity_residual_momentum_v3.py:EquityResidualMomentum1dV3` |
| Base | `EquityResidualMomentum1dV2` (weekly) + MH_RAW multi-horizon rank consensus |
| Signal | **12-1 residual momentum positive gate** → equal-mean percentile ranks of raw residual momentum at **6-1 / 9-1 / 12-1 months** (formation `126 / 189 / 252` residual bars, `skip 21`, `beta window 273`) `standardized=false` → `ivol q=0.70` rejection → `top_n 8` → inverse-vol sizing `max_single_weight 0.10` → production risk overlay unchanged |
| `FROZEN_V3_PARAMS` | `formation_bars 252, skip_bars 21, beta_window_bars 273, residual_vol_window 63, top_n 8, max_single_weight 0.10, ivol_reject_quantile 0.70, exclude [SPY], rebalance weekly` |
| Promotion | `docs/mh_raw_v3_paper_promotion_2026_08.md` — PAPER `v2 → v3` equity selection applied; PAPER soak `scripts/audit_v3_paper_soak.py` |
| Economics change | None — same allocation, same risk, same execution, same universe, same costs; only equity selection identity changes |
| LIVE | Disabled — `config/settings.yaml live.enabled false`, `tests/test_live_safety.py` guard |

No parameter sweep or V4 is permitted on this lineage.

---

## 2. Symmetric PIT Methodology

| Aspect | Detail |
|---|---|
| Dataset | `data/historical/equity_pit_symmetric_2016_2026_v5.csv` + `equity_entity_lifetimes_full_v5.csv` |
| Rows / symbols | 11,686,832 rows, 9,010 entities (6,858 active + 2,152 inactive), 2016-01-04 → 2026-07-31 |
| Identity | Provider asset UUID (zero placeholders), exhaustive active+inactive × NASDAQ/NYSE/AMEX/ARCA/BATS, SIP split-adjusted price |
| Exclusions | `VERIFIED_TICKER_REUSE` resolved (keep active or longest inactive; 29 entities excluded), no future bars, no survivorship bias |
| Universe construction | **Production PIT** — `src/parayabuck/backtest/product.py:build_product_universe` → `Stage-A` eligibility (`minimum_price 5`, `minimum_median_dollar_volume 20M`, `minimum_data_completeness 0.95`, `minimum_atr_pct 0.005`, `maximum_atr_pct 0.12`) → `target_shortlist 300` by `-median_dollar_volume` |
| Scanner parity | Historical `build_trailing_point_in_time_universe` sliding window matches live `EquityUniverseScanner.equity_candidate_from_bars` on 20-bar trailing median dollar volume + ATR + completeness; period `weekly` at Monday effective_at |
| Audit | `scripts/audit_symmetric_stage_a_coverage.py` — exact 492-Monday Stage-A simulation (2017-01-02 → 2026-07-31), breadth `active_outside_old 606` entrants beyond old 1,194 Stage-A pool |
| Corporate actions | `data/historical/equity_survivorship_full_corporate_actions_v5.csv` 141,908 events (cash dividends 141,908 + 61 stock dividends + 270 forward splits + 1,207 reverse splits) vs old 16,772 |
| Dividends | Cash dividends applied as `dividend_cash` (not price); splits adjust price point-in-time per `BarStore` + `data/adjustment.py merge_provider_authority` |

---

## 3. Dataset & Corporate-Actions SHAs (Canonical)

| File | SHA256 | Size | Manifest |
|---|---|---|---|
| `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` | 882,354,549 B | `equity_pit_symmetric_2016_2026_v5.manifest.json` (`ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` lifetimes) |
| `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` | 14,713,370 B | `equity_survivorship_full_corporate_actions_v5.manifest.json` |

Large CSVs are gitignored (`data/historical/equity_survivorship_aware_*.csv` etc) but manifests are committed. The v4 lineage SHA `763fd853729272c76a85202cf29c079da20dcb0380c6e2b652a584b68643de54` (3,554 symb) is retained in `data/research_manifests/full_survivorship_reconstruction_2026_08.json` (D_FULL, superseded) for audit.

---

## 4. Production PIT Scanner Parity

- Historical engine receives the **same** `universe` and `universe_version = hash_point_in_time_universe(universe)` as the production scanner would at that timestamp. Verified by `data/research/full_survivorship_2026_08/parity_canary.json` (`old_universe_hash 4f253478...`, `v5_universe_hash c6f8344b...`) and `universe_audit_v5.json` (weekly sampled, 500 sampled sorted effective_at).
- No stale survivor pool: `build_trailing_point_in_time_universe` never uses future bars; each decision sees exactly the trailing 20-bar window that would have been available live at `bar.timestamp`.
- Signal uses split-adjusted bars (`adjustment='splits'`) but CA cash dividends are added separately — price and income are not conflated, verified by `tests/test_pit_universe_parity.py` (`CA parity OLD 16,772 vs FULL 141,908 dividends` same `effective_params`).

---

## 5. Terminal T1 Definition (Canonical) — OLD_T0 vs SYMMETRIC_FULL_T1

| Run | Universe | Terminal policy | Meaning | Engine |
|---|---|---|---|---|
| `OLD_T0` | 1,194 survivors `equity_1200_2016_2026.csv` (`universe_hash 4f2534...`) | `T0` stale reference (diagnostic) | No lifecycle — stale `last_price` persists; not comparable to PIT but retained as survivor-biased reference | `PortfolioBacktestEngine` |
| `SYMMETRIC_FULL_T0` | 9,010 symmetric (`c6f8344b...`, 141,908 dividends) | `T0` stale | Same as OLD_T0 but on full bars without terminal lifecycle — overstates exposure (≈0.68) and holdings (≈7.38) | same |
| **`SYMMETRIC_FULL_T1`** | **9,010 symmetric** | **`T1`** `LAST_TRADABLE_EXIT` — held terminal position closed at last observed close minus normal sell costs (TAF/CAT/SEC per `backtest/regulatory_fees.rates_for_trade_date`), position removed, no future SELL, no stale value | **CANONICAL historical estimate** — path-dependent `TerminalAwareResearchEngine` (`E_FULL` freeze, `RESEARCH-ONLY` under `src/parayabuck/backtest/`) | `TerminalAwareResearchEngine` |
| `SYMMETRIC_FULL_T2` | 9,010 | `T2` 50% recovery + costs (unresolved non-successor) | Severe-loss sensitivity bound | same |
| `SYMMETRIC_FULL_T3` | 9,010 | `T3` zero recovery | Zero-survivor lower bound | same |

Exclusions (no haircut): `VERIFIED_RENAME` / successor continuation / `DATA_GAP` / `TEMPORARY_HALT`. Only `should_apply_haircut=true` entities with terminal event after final observed tradable session are lifecycle-executed, exactly once, after the bar.

### 5.1 The canonical numbers (10.5y, $100k, T1, 1× costs, weekly, 1d signal)

From `data/research/full_survivorship_2026_08/full_v3_results_v5.json`:

| Metric | `OLD_T0` (survivor-biased ref) | **`SYMMETRIC_FULL_T1` (CANONICAL)** | `SYMMETRIC_FULL_T0` (stale) |
|---|---|---|---|
| `total_return` | `+172.0%` (`ending 272,015`) | **`+113.6%` (`213,642`)** | `+97.1%` (`197,116`) |
| `cagr` | `9.93%` | **`7.44%`** | `6.63%` |
| `sharpe` | `0.796` | **`0.639`** | `0.600` |
| `sortino` | `1.016` | **`0.827`** | `0.764` |
| `calmar` | `0.451` | **`0.327`** | `0.300` |
| `maxDD` | `22.0%` (1,070d) | **`22.7%` (1,084d)** | `22.1%` (1,112d) |
| `avg_holdings` | `5.03` | **`4.92`** | `7.38` |
| `avg_exposure` | `0.496` | **`0.482`** | `0.679` |
| `turnover` | `143.9` | **`146.7`** | `135.7` |
| `fees` | `7,596` | **`6,157`** | `5,582` |
| `dividend_cash` | `17,240` | **`10,881`** | `10,295` |
| `terminal_events` | `0` | **`6`** (`76561 proceeds`) | `0` |
| `ca_count` | `16,772` | **`141,908`** | `141,908` |

Labels are explicit per mission to avoid stale-number confusion:

- `OLD_T0 = survivor-biased reference` (1,194 survivors, not PIT, inflates CAGR by +2.5pp and Sharpe by +0.16 vs canonical).
- `SYMMETRIC_FULL_T1 = canonical historical estimate` — **use this as the single current result.**
- `T2/T3 = terminal sensitivity bounds` — see §7.

The demo-era `survivorship_aware_2016_2026_v2.csv` numbers (1,418 symb, `+143%` etc) and the v3 stitched `survivorship_aware_full_*_v3.csv` numbers are **not current** and are archived under `data/research/archive/2026_08/`.

---

## 6. 20-Fold Summary (Real Independent Walk-Forward)

From `old_vs_full_walk_forward_real_v5.json` — **not** the synthetic `old_vs_full_walk_forward.json` derivative (deleted).

| Property | Detail |
|---|---|
| Method | 20 independent calendar 6-month folds tiling `2017-01-03 .. 2026-07-30` (`fold_step_months 6`, `relativedelta`), each an independent `PortfolioBacktestEngine` + `TerminalAwareResearchEngine` with PIT universe + corrected CA + T1 lifecycle; no overlapping warmup, no shared state |
| Pairing | `paired_fold_deltas` — same calendar window, same costs, same `FROZEN_V3_PARAMS`; only universe differs (OLD vs FULL) |
| What is **not** synthetic | `selection_overlap_real.json` is weekly V3 decision-level Jaccard + weight deltas across the 20 folds (not CSV-universe overlap); `terminal_event_accounting_audit_v5.json` is per-entity held-at-termination audit (not cohort estimate) |
| Old bias measured | Median OLD outperforms FULL by ≈+1–2pp per fold on return (survivorship lift), consistent with §5 `OLD_T0` vs `SYMMETRIC_FULL_T1` gap |
| Robustness | `explanation_metadata` + `terminal_policy_semantics.json` + `parity_canary.json` prove no future SELL after terminal, quantity==0, no stale value leakage |
| Result | Historical V3 data robustness **STRONG** per `final_verdict_v5.json` (`quality_geometry_passed true`, `active_outside_old 606`, `avg_holdings 4.92`, `avg_exposure 0.48`, `calmar 0.327`) — not just “MODERATE” as in D_FULL `full_survivorship_reconstruction_2026_08.json` |

---

## 7. Known Limitations

| Limitation | Impact | Reference |
|---|---|---|
| **10.5y single-history backtest** | Point estimate `SYMMETRIC_FULL_T1` is a single 10.5y sample; no out-of-history guarantee. Bootstrap 2,000 × 20-day blocks and jackknife are in `mh_raw_promotion_2026_08/` but are not a forward guarantee | `mh_raw_promotion_validation_2026_08.md` (archived) |
| **Terminal bounds are severe** | `FULL_T2` (+58.6% / 4.46% CAGR / Sharpe 0.40 / maxDD 33.5%) and `FULL_T3` (+15.9% / 1.40% / 0.17 / 43.2%) are lower bounds if held delistings recover 50% or 0%; T2/T3 are not point estimates | `terminal_sensitivity_v5.json` |
| **Survivorship-corrected does not remove regime risk** | 2022 rate-hike drawdown and 2024-2025 breadth changes are still embedded; `capital_utilization_cadence_verdict.md` flags equity weekly edge is partly ASX-driven, crypto 96h edge thin | `docs/capital_utilization_cadence_verdict.md`, `docs/optimization_tournament_2026_08.md` |
| **Dividend price vs income** | v5 splits adjust price; dividends add cash. The `equity_1200_dividends.csv` ledger is 1,194-only and incomplete for FULL — v5 uses its own 141,908-event CA; `equity_survivorship_full_corporate_actions_v4.csv` is superseded | `data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json` |
| **Universe construction choices** | `target_shortlist 300` → `maximum_stream_symbols 30` + sector cap 5, volatility optimum 0.03, Stage-A weekly. Different shortlist or period would change snapshots (see `audit_symmetric_stage_a_coverage.py` 492-Monday sweep) | `config/settings.yaml universe.equity.scanner` |
| **Cost assumptions** | `1×` is `slippage 2bps + spread 2bps + effective-dated SEC/CAT/TAF`; stress `2×/3×/5×` in `mh_raw_promotion` and `equity_hardening` shows advantage persists at `3×` but with higher maxDD | `backtest/regulatory_fees.py`, `benchmarks/parity_guard.json economics.fee_total` |
| **No live guarantee** | Backtest economics do not imply forward live returns; `PAPER` forward validation is the next gate, not a return claim | `docs/paper_operations.md` |

---

## 8. Final Verdict

| Field | Value |
|---|---|
| Verdict | `SYMMETRIC PIT RECONSTRUCTION VALIDATED — V3 DATA ROBUSTNESS STRONG` |
| Verdict file | `data/research/full_survivorship_2026_08/final_verdict_v5.json` (`historical_status: HISTORICAL V3 RESEARCH FROZEN — FINAL`, `canonical_baseline: SYMMETRIC_FULL_T1`) |
| Superseded verdict | `data/research_manifests/full_survivorship_reconstruction_2026_08.json` (`D_FULL`) — “MODERATE” is **SUPERSEDED** pending E→H corrections (terminal lifecycle, real walk-forward, entity-safe reuse, CA parity, selection overlap, symmetry audit) per `acc11e6` |
| Quality geometry | `quality_geometry_passed true` — see §5 `avg_holdings 4.92`, `avg_exposure 0.48`, `max_exposure 0.60 cap`, `max_holdings 6`, `CAGR/Sharpe/Calmar` as above |
| Promotion | `mh_raw_v3_paper_promotion_2026_08.md` PASS — `equity_residual_momentum_v3` frozen for PAPER, no V4 |
| Historical research status | `HISTORICAL V3 RESEARCH FROZEN` — no sweeps, no V4, no rerun optimization |
| PAPER next phase | Forward validation via `scripts/audit_v3_paper_soak.py` + `PortfolioStateRepository` latches, `docs/paper_operations.md` weekly 1d + 96h crypto, `optimization_tournament_2026_08.md` parity-verified 22 Tier-1 keeps |
| Archived prior research | `docs/archive/research_2026_08/README.md` + `data/research/archive/2026_08/README.md` index 12 superseded docs / 15 data dirs + 9 deleted D scripts |
| Reproducibility | `scripts/run_symmetric_v5_validation.py` (canonical) or `scripts/research/run_v3_canonical_validation.py` (shim), with `build_symmetric_v5_pipeline.py` + `fetch_corporate_actions_v5.py` + `audit_symmetric_stage_a_coverage.py`; hashes pinned §3 |
| LIVE | `DISABLED` |

---

## 9. Reproducibility — One Canonical Command

```bash
# Full symmetric validation (10.5y, 6 path-dependent runs, 20-fold, can take hours):
python scripts/run_symmetric_v5_validation.py
# Shorthand shim (delegates to the same frozen runner):
python scripts/research/run_v3_canonical_validation.py
# Dataset manifests verify SHAs on load:
python -c "import json,hashlib;print(json.load(open('data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json'))['sha256'])"
# Parity guard:
cat benchmarks/parity_guard.json
cat data/research/full_survivorship_2026_08/parity_canary.json
```

Intermediate runners (`corrected_v3_runner.py` / `run_corrected_v3.py` / `run_v3_survivorship_comparison.py`) and temp generators (`build_full_resume_batched.py` etc) are archived or deleted (see `docs/repository_cleanup_inventory_2026_08.md` §§5–7 and `docs/archive/research_2026_08/README.md`). Do not use them; they produce incompatible v2/v4 results or synthetic placeholders.

---

## 10. Preservation

- `data/research_manifests/full_survivorship_correction_2026_08.json` (E_FULL) → `_F` → `_G` → `_H` is the frozen correction chain. Keep it; `full_survivorship_reconstruction_2026_08.json` (D_FULL) is superseded but retained for audit.
- Do not delete `equity_pit_symmetric_2016_2026_v5.csv` / its manifest or `equity_survivorship_full_corporate_actions_v5.csv` SHA manifests — they identify the final v5 dataset. Large CSVs remain gitignored; manifests are the identifier.
- `src/parayabuck/backtest/terminal_aware_engine.py` + `terminal_policy.py` are `RESEARCH-ONLY` (header banners) and are not imported by production/PAPER/LIVE; they are retained in-place to preserve frozen K/L validation without a new top-level `research/` package.
- `docs/repository_cleanup_inventory_2026_08.md` is the pre-move inventory; `docs/archive/research_2026_08/README.md` is the post-archive index.

---

*Document version 1.0 — 2026-08-26. This document supersedes all prior docs that presented `OLD_T0` or `DEMO_T0` as current results. See `docs/repository_cleanup_inventory_2026_08.md` for the complete move/delete table and `benchmarks/parity_guard.json` for the machine-readable freeze.*
