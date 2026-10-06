# Research index

Current production identity: **VT35** (`equity_leveraged_trend_v1`, QLD while
QQQ > 200-session SMA, 35% vol target), PAPER only. Supporting code, data and
results for each doc live in `research/<name>/` unless noted.

| Doc | Date | One-line conclusion | Status |
|---|---|---|---|
| [`sip_recent_data_clamp_v1.md`](sip_recent_data_clamp_v1.md) | 2026-09-30 | Fix: SIP historical requests (warmup, hourly split-basis verifier) are clamped to now − `sip_embargo_minutes`, and Alpaca Basic's recent-SIP-data error no longer triggers the permanent SIP→IEX history downgrade. | current evidence |
| [`signal_cross_check_v1.md`](signal_cross_check_v1.md) | 2026-09-30 | Runtime guard: the decision is recomputed on the Alpaca SIP daily bar and a trend disagreement with the IEX stream bar holds the position; historically 0 disagreements in 2,406 sessions (SIP vs Yahoo), ~0.1–0.4 holds/yr under modelled IEX close noise; Stage B unchanged. | current evidence |
| [`crypto_removal_v1.md`](crypto_removal_v1.md) | 2026-09-30 | Crypto code, config and tests deleted (equity-only); Stage B reproduces S1 exactly (NAV 1,173,537.61, identical hashes); config digest changes. | current evidence |
| [`paper_config_v2.md`](paper_config_v2.md) | 2026-09-29 | Adopt S1: crypto sleeve disabled, equity 0.93 → 0.98; BIL idle-cash sweep fails its 2x-cost gate and ships off. | current evidence |
| [`crypto_sleeve_decision_v1.md`](crypto_sleeve_decision_v1.md) | 2026-09-29 | Crypto 0.05 sleeve adds ~+1 pp/yr (t ≈ 0.8) with more drawdown and large ops burden; remove (config-disable first). | current evidence |
| [`execution_model_audit_v1.md`](execution_model_audit_v1.md) | 2026-09-29 | Stage B and live share the execution model (decide at close, fill next open); no backtest bug; live gaps are inputs (IEX close, missed sessions, latch fixed in ebcae9c). | current evidence; superseded in part (§1 live column and §4 predate waves 2a/2b: early-close flush, deferred-decision retry, forming-bar catch-up fix) |
| [`rule_variants_v1.md`](rule_variants_v1.md) | 2026-09-29 | No VT35 rule variant (exit buffers, min hold, staging, gold, T-bill parking) passes the pre-registered gate; nothing to Stage B. | current evidence |
| [`stress_robustness_v1.md`](stress_robustness_v1.md) | 2026-09-29 | VT35 passes the small-LIVE bar on 1999–2026 history (13.1% vs SPY 8.3% CAGR, DD 47% vs 55%) but loses to SPY under short-persistence bootstrap; its BIL/SGOV adoption was later overruled by `paper_config_v2`. | current evidence |
| [`profit_max_v1.md`](profit_max_v1.md) | 2026-09-25 (re-run 2026-09-28) | VT35 chosen as PAPER equity identity: Stage B 28.98% CAGR / 29.2% max DD vs VOO 14.5%, 2017–2026. | current evidence |

Other research artifacts still in the tree:

- `data/historical/stress_robustness_v1_*.csv` — inputs to `stress_robustness_v1`.
- `data/historical/equity_entity_lifetimes_full_v5.csv` and the two `*_v5.manifest.json` files — canonical v5 equity inputs needed to rerun Stage B (`research/paper_config_v2/run_stage_b.py`).

## Pruned 2026-09-30

Everything from the pre-VT35 research era (residual momentum V1/V2/V3, sleeves,
account-capacity / cap-8 work, physical clock, public-data alpha search,
correctness fix reports, receipts and closure docs, release evidence v2, old
v2–v4 survivorship data) was removed from `main`. It is all reachable from the
local annotated tag **`archive/research/pre-prune-2026-09-30`** (commit
`1d55ef2`), for example:

```sh
git show archive/research/pre-prune-2026-09-30:docs/research/research_register.md
git archive archive/research/pre-prune-2026-09-30 research/global_slot_accounting_fix_v1 | tar -x -C /tmp/x
```

Removed docs: `research_register.md`, `research_archive_inventory.md`,
`canonical_v3_baseline.md`, `equity_universe_limitations.md`,
`public_data_research_policy_v1.md`, `core_satellite_combination_policy_v1.md`,
`account_capacity_architecture_decision_v1.md`,
`account_capacity_migration_v{1,2}_closure.md`, `cap8_main_integration_v1.md`,
`CAP8_*_RECEIPT.json`, `FINAL_REPOSITORY_CLEANUP_RECEIPT{,_V2}.json`,
`physical_availability_clock_v2_closure.md`, `physical_clock_canonical_stop.md`,
`etf_diversifier_v1_closure.md`, `sleeve_candidates_v2_v3_closure.md`,
`dividend_sleeve_production_v1.md`, `gold_sleeve_production_v1.md`.
Removed trees: `research/{account_capacity_architecture_audit_v1,
alpaca_open_order_pagination_fix_v1, crypto_position_cap_correctness_v1,
global_slot_accounting_fix_v1, month_end_first_open_correctness_v1,
paper_observability_repair_v1, production_correctness_cap8_readiness_v1,
production_restart_reconciliation_fix_v1}`, `data/release_evidence_v2/`,
`RESERVED_ROLE_DEFECT_RECEIPT.md`, and superseded `data/historical/` files
(`equity_1200_*`, entity lifetimes v2–v4, survivorship v2–v4 manifests,
corporate actions v4). Older per-campaign evidence is also behind the
individual `archive/research/*`, `archive/audit/*`, `archive/production/*` and
`archive/maintenance/*` tags (`git tag -l 'archive/*'`).

## Removed with the crypto code 2026-09-30

`canonical_crypto_baseline.md` (causal-execution crypto replay authority,
+5.42% total 2021–2026), `maintenance/crypto_canonical_causal_execution_v1/`
and `data/historical/crypto_expanded_1h.csv` are reachable from the local tag
**`archive/code/crypto-sleeve-2026-09`** (commit `1d55ef2`), together with all
crypto code; see [`crypto_removal_v1.md`](crypto_removal_v1.md).
