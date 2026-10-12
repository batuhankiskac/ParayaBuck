# Research index

Current production identity: **VT35 with the E2 trend vote**
(`equity_leveraged_trend_v1` 1.1.0, QLD while at least two of QQQ > 200-session
SMA, QQQ 252-session return > 0 and QQQ > 126-session channel midpoint hold,
35% vol target), PAPER only. Supporting code, data and
results for each doc live in `research/<name>/` unless noted.

| Doc | Date | One-line conclusion | Status |
|---|---|---|---|
| [`tbill_sweep_v1.md`](tbill_sweep_v1.md) | 2026-10-12 | BIL only while E2 is off (S1) fails the block-20 bootstrap (−0.08 pp; each flip costs a BIL round trip); S2_10 (S1 plus residual `0.98 − w` in BIL, re-sized only outside a 10 pp band) passes every gate: 14.88% vs 14.67% CAGR, max DD 46.0% vs 49.6%, bootstrap +0.18 pp, ^GSPC 1957–99 +1.6 pp; Stage B +0.42 / +0.30 pp at 1x / 2x. Not integrated; an Alpaca cash sweep at ≈ DTB3 − 0.5 pp would add more (+0.68 pp) with no code. Evidence: tag `archive/research/tbill-sweep-v1-2026-10`. | current evidence |
| [`e2_integration_v1.md`](e2_integration_v1.md) | 2026-10-12 | E2 shipped as `equity_leveraged_trend_v1` 1.1.0 (`trend_rule: majority_sma_tsm_don`; owner-approved exception to the failed bootstrap gates) with price-only TSM (zero hurdle) and DON, since production has no total-return or T-bill series (−0.15 pp vs research E2 on 2000–2026); Stage B 2017–2026 at 1x: 31.05% CAGR / 32.19% max DD vs VT35 29.35% / 27.70% (S1 reproduced exactly). | current evidence |
| [`vix_filter_v1.md`](vix_filter_v1.md) | 2026-10-12 | No VIX overlay on VT35 (cash while VIX > 30, VIX/SMA60 > 1.5, or VIX > VIX3M; or VXN-implied vol sizing) passes under base and calibrated financing plus a ^GSPC+^VIX 1990–1999 check: all lose 0.7–3.2 pp CAGR and bootstrap median; the spike and term-structure filters cut the 2020 drawdown (27.6% → 12.7%) but whipsaw elsewhere; VIX > 30 deepens max DD to 56.2%; VT35 unchanged. | current evidence |
| [`e2_confirmation_v1.md`](e2_confirmation_v1.md) | 2026-10-12 | `signal_ensemble_v1`'s E2 (2-of-3 SMA200/TSM252/DON126) vs C0 on ^IXIC 1975–99, ^GSPC 1935–56, ^N225, ^GDAXI, ^FTSE: passes K1–K4 (E2 ≥ C0 in 4/5, median +0.37 pp) but fails K5 (pooled block-20 bootstrap median −0.001 pp); E2 closed, VT35 unchanged. | current evidence |
| [`signal_ensemble_v1.md`](signal_ensemble_v1.md) | 2026-10-12 | No mixed-type trend arm (SMA200 / 252-day TSM / 126-day Donchian: 2-of-3 majority, proportional, TSM alone, Donchian alone) passes on 2000–2026 under base and calibrated financing plus a ^GSPC 1957–1999 out-of-sample check; majority E2 (+1.4 pp CAGR, same DD) misses only the block-20 bootstrap gate by 0.05 pp; VT35 unchanged. | current evidence |
| [`gold_riskoff_oos_v1.md`](gold_riskoff_oos_v1.md) | 2026-10-12 | Out-of-sample test (1975–1998, Bundesbank Frankfurt gold fix, price indices) of `leverage_frontier_v1`'s gold-in-risk-off arm fails every gate: on ^IXIC GOLD 14.3% vs CASH 17.4% CAGR, max DD 57.9% vs 44.3%, bootstrap median 19.3% vs 19.8%; also loses on ^NDX and ^GSPC; gold risk-off closed; VT35 unchanged. | current evidence |
| [`leverage_frontier_v1.md`](leverage_frontier_v1.md) | 2026-10-12 | No leverage (1–3x) × vol-target (30–50%, none) cell passes on 2000–2026: every cell clearing +1 pp CAGR fails the bootstrap tail gate (P(DD>70%)), under base and calibrated financing; risk-off BILLS/BOND10/GOLD all fail (GOLD +2.3 pp but DD +4.3 pp); VT35 unchanged. | current evidence |
| [`timesfm_v1.md`](timesfm_v1.md) | 2026-10-12 | No TimesFM 2.5 arm (vol forecast, trend replacement, exit filter) passes the pre-registered gate on 2000–2026; the TimesFM vol arm loses to plain EWMA vol, and the direction arms lose 5.5–6.1 pp CAGR. The EWMA comparator V0 passes (+0.11 pp CAGR, max DD 46.1% → 43.7%) but is not promoted; VT35 unchanged. | current evidence |
| [`trend_robustness_v2.md`](trend_robustness_v2.md) | 2026-10-11 | No arm (SMA 100–250 ensemble, QLD+SSO two-index split, both) passes the pre-registered gate on 2000–2026; all give up 1.2–2.7 pp CAGR; VT35 unchanged. | current evidence |
| [`sip_recent_data_clamp_v1.md`](sip_recent_data_clamp_v1.md) | 2026-09-30 | Fix: SIP historical requests (warmup, hourly split-basis verifier) are clamped to now − `sip_embargo_minutes`, and Alpaca Basic's recent-SIP-data error no longer triggers the permanent SIP→IEX history downgrade. | current evidence |
| [`signal_cross_check_v1.md`](signal_cross_check_v1.md) | 2026-09-30 | Runtime guard: the decision is recomputed on the Alpaca SIP daily bar and a trend disagreement with the IEX stream bar holds the position; historically 0 disagreements in 2,406 sessions (SIP vs Yahoo), ~0.1–0.4 holds/yr under modelled IEX close noise; Stage B unchanged. | current evidence |
| [`crypto_removal_v1.md`](crypto_removal_v1.md) | 2026-09-30 | Crypto code, config and tests deleted (equity-only); Stage B reproduces S1 exactly (NAV 1,173,537.61, identical hashes); config digest changes. | current evidence |
| [`paper_config_v2.md`](paper_config_v2.md) | 2026-09-29 | Adopt S1: crypto sleeve disabled, equity 0.93 → 0.98; BIL idle-cash sweep fails its 2x-cost gate and ships off. | current evidence |
| [`crypto_sleeve_decision_v1.md`](crypto_sleeve_decision_v1.md) | 2026-09-29 | Crypto 0.05 sleeve adds ~+1 pp/yr (t ≈ 0.8) with more drawdown and large ops burden; remove (config-disable first). | current evidence |
| [`execution_model_audit_v1.md`](execution_model_audit_v1.md) | 2026-09-29 | Stage B and live share the execution model (decide at close, fill next open); no backtest bug; live gaps are inputs (IEX close, missed sessions, latch fixed in ebcae9c). | current evidence; superseded in part (§1 live column and §4 predate waves 2a/2b: early-close flush, deferred-decision retry, forming-bar catch-up fix) |
| [`rule_variants_v1.md`](rule_variants_v1.md) | 2026-09-29 | No VT35 rule variant (exit buffers, min hold, staging, gold, T-bill parking) passes the pre-registered gate; nothing to Stage B. | current evidence |
| [`stress_robustness_v1.md`](stress_robustness_v1.md) | 2026-09-29 | VT35 passes the small-LIVE bar on 1999–2026 history (13.8% vs SPY 8.3% CAGR, DD 49.6% vs 55.2%) but loses to SPY under short-persistence bootstrap; its BIL/SGOV adoption was later overruled by `paper_config_v2`. | current evidence |
| [`profit_max_v1.md`](profit_max_v1.md) | 2026-09-25 (re-run 2026-09-28) | VT35 chosen as PAPER equity identity: Stage B 28.98% CAGR / 29.2% max DD vs VOO 14.5%, 2017–2026. | current evidence |

Other research artifacts still in the tree:

- `data/historical/stress_robustness_v1_*.csv` — inputs to `stress_robustness_v1` (reused by `trend_robustness_v2`; DTB3 also by `gold_riskoff_oos_v1`).
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
