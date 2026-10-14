# Research index

Current production identity: **VT35 with the E2 trend vote**
(`equity_leveraged_trend_v1` 1.1.0, QLD while at least two of QQQ > 200-session
SMA, QQQ 252-session return > 0 and QQQ > 126-session channel midpoint hold,
35% vol target), PAPER only. Supporting code, data and
results for each doc live in `research/<name>/` unless noted (from 2026-10-12 on,
in the archive tag named in the row).

| Doc | Date | One-line conclusion | Status |
|---|---|---|---|
| [`tax_asym_v2.md`](tax_asym_v2.md) | 2026-10-14 | Clean after-tax bootstrap of `tax_frontier_v1`'s A2 (real TRY calendar 2016–26 and 2006–16, plus a flat 20% tax; market returns resampled): median after-tax Δ +1.05 / +0.39 / +1.86 pp but A2 wins only 53–62% of paths (gate 65%) with much deeper drawdowns; A2 closed. Evidence: tag `archive/research/tax-asym-v2-2026-10`. | current evidence |
| [`tax_asym_v1.md`](tax_asym_v1.md) | 2026-10-14 | Confirmation of A2: threshold plateau (40/60%) and the 18-ETF drawdown test pass, pre-tax G2/G5 fail (DD +5 pp, bootstrap −0.36 pp); its after-tax bootstrap was broken (643/1000 paths negative from block-resampled USD/TRY), superseded by `tax_asym_v2`. Evidence: tag `archive/research/tax-asym-v1-2026-10`. | superseded by `tax_asym_v2` |
| [`tax_hedge_v1.md`](tax_hedge_v1.md) | 2026-10-14 | Buying an inverse hedge (TQQQ*/SQQQ*) instead of trimming winners does not cut Turkish tax: H1 loses 0.44–0.53 pp after tax in all three windows (168 sells/yr; hedge losses don't offset long-leg gains). Evidence: tag `archive/research/tax-hedge-v1-2026-10`. | current evidence |
| [`tax_frontier_v1.md`](tax_frontier_v1.md) | 2026-10-14 | After-tax (Turkish resident, $100k) comparison of E2-family rules: C0 9.53% after tax (14.67% pre-tax, 57 sells/yr); A2 (downward vol resizes only when target < 50% of held weight) 11.15% / 16.58% / 27.05% vs C0 9.53 / 14.50 / 22.69 on 2000 / 2010 / 2017 windows, DD 54.5% vs 49.6%, but pre-tax bootstrap −0.36 pp; no vol target (A1) DD 81%; SPY 7.19%. A2 later failed `tax_asym_v1/v2`. Evidence: tag `archive/research/tax-frontier-v1-2026-10`. | current evidence (A2 closed by `tax_asym_v2`) |
| [`riskoff_after_tax_v1.md`](riskoff_after_tax_v1.md) | 2026-10-14 | Multi-instrument extension of the `after_tax_tr_v1` model (reconciles C0 exactly): L2's +0.82 pp pre-tax edge is +0.04 pp after tax at $100k (2000–2026), −0.32 pp at $1M, +0.43 pp since 2017; the extra tax comes from gold-leg gains. L2 closed for the owner. Evidence: tag `archive/research/riskoff-after-tax-v1-2026-10`. | current evidence |
| [`riskoff_rotation_v4.md`](riskoff_rotation_v4.md) | 2026-10-14 | Independent test of L2 on 21 real ETFs (sector SPDRs, country ETFs, EFA, EEM, SPY, DIA, IWM; real IEF/GLD defensives, 2005–2026): median +1.01 pp CAGR, 20/21 positive, bootstrap +0.84 pp, doubled cost +0.43 pp, but max DD worse by > 3 pp on 5/21 (SPY, DIA, XLB, XLU, XLV; gate 80%) — fails F2. Evidence: tag `archive/research/riskoff-rotation-v4-2026-10`. | current evidence |
| [`riskoff_rotation_v3.md`](riskoff_rotation_v3.md) | 2026-10-14 | Low-turnover R4: L2 (defensive choice monthly, 10 pp resize band) passes v2's gates incl. the doubled-cost stress: 15.49% vs 14.67% CAGR, DD 44.2% vs 49.6%, bootstrap +0.78 pp, OOS median +1.11 pp; turnover 11.4/yr, ~62 sells/yr. Later failed `riskoff_rotation_v4` (DD) and after tax. Evidence: tag `archive/research/riskoff-rotation-v3-2026-10`. | current evidence |
| [`riskoff_rotation_v2.md`](riskoff_rotation_v2.md) | 2026-10-14 | Audit + OOS of R4 (no bugs; BOND10* vs IEF corr 0.96): passes G1–G8 (5 OOS windows, median +0.96 pp) but fails the doubled-cost turnover stress (13.81% vs 14.13%). Evidence: tag `archive/research/riskoff-rotation-v2-2026-10`. | current evidence |
| [`riskoff_rotation_v1.md`](riskoff_rotation_v1.md) | 2026-10-14 | Trend-gated defensive asset while E2 is off: R4 (better of BOND10*/GOLD by 252-session return if above T-bills, 10% vol sizing) 14.99% vs 14.67%, DD 42.0% vs 49.6%, bootstrap +0.54 pp, but turnover 2.2× and OOS not run; gold-only R2 fails. Evidence: tag `archive/research/riskoff-rotation-v1-2026-10`. | current evidence |
| [`range_vol_v3.md`](range_vol_v3.md) | 2026-10-14 | Independent replication of range-based vol (G3x 14.99%, YZ20 14.80% reproduced) and production-feasible variants: rolling 252/504 ratios lose to C0, a frozen ratio (R* 1.127) passes the main window but worsens DD on 9/18 ETFs, plain Parkinson raises DD to 52.6%; nothing implementable passes; range vol closed. Evidence: tag `archive/research/range-vol-v3-2026-10`. | current evidence |
| [`range_vol_v2.md`](range_vol_v2.md) | 2026-10-14 | Confirmation of range vol: basis control C0q = C0; Parkinson with an expanding close-to-close/Parkinson ratio (G3x) passes all gates (+0.31 pp, 16/18 ETFs positive), Yang-Zhang fails the ETF share gate; but G3x's expanding ratio needs full history since 1999, which production lacks (see `range_vol_v3`). Evidence: tag `archive/research/range-vol-v2-2026-10`. | superseded by `range_vol_v3` |
| [`range_vol_v1.md`](range_vol_v1.md) | 2026-10-14 | Range-based vol estimators (Yang-Zhang, Garman-Klass, Parkinson) on QQQ OHLC beat vol20 sizing by +0.13–0.31 pp with 1–2.7 pp lower DD on 2000–2026 and SPY/DIA/IWM; no selection rule pre-registered and a YZ NaN bug (fixed in v2). Evidence: tag `archive/research/range-vol-v1-2026-10`. | superseded by `range_vol_v3` |
| [`vote_hysteresis_v1.md`](vote_hysteresis_v1.md) | 2026-10-14 | Asymmetric vote-count thresholds on E2 (enter 3/3 exit ≤1/3, exit only at 0/3, confirmation days) fail: they cut round trips and sells but lose 0.05–1.7 pp CAGR or deepen DD to 57%. Evidence: tag `archive/research/vote-hysteresis-v1-2026-10`. | current evidence |
| [`shortside_v1.md`](shortside_v1.md) | 2026-10-14 | Holding an inverse Nasdaq fund (PSQ*/QID*) in downtrends fails: all arms lose the bootstrap (−2.0 to −7.5 pp) and OOS; the low-vol-only short arm H4 looks good in-sample (+1.09 pp, DD −8 pp) but is a whipsaw fluke. Evidence: tag `archive/research/shortside-v1-2026-10`. | current evidence |
| [`overnight_hold_v1.md`](overnight_hold_v1.md) | 2026-10-14 | Holding E2's position only overnight (close-auction buy, open-auction sell) fails decisively: ~335 trades/yr make it −18% CAGR at the production cost model; the raw QQQ overnight edge (+10.5%/yr gross) is unstable by year. Evidence: tag `archive/research/overnight-hold-v1-2026-10`. | current evidence |
| [`relstrength_v1.md`](relstrength_v1.md) | 2026-10-14 | Rotating between QLD* and SSO* by 126/252-session relative strength (with or without per-index E2) loses 1.2–4.1 pp CAGR and the bootstrap/OOS (^IXIC vs ^GSPC 1975–99, ^GDAXI vs ^FTSE). Evidence: tag `archive/research/relstrength-v1-2026-10`. | current evidence |
| [`dip_overlay_v1.md`](dip_overlay_v1.md) | 2026-10-14 | Raising exposure after sharp short-term drops in an uptrend (5-day −5%, RSI(2)) or trimming after spikes fails: dip arms add 7–9 pp DD and lose the bootstrap; trims lose 2017+ and OOS. Evidence: tag `archive/research/dip-overlay-v1-2026-10`. | current evidence |
| [`exec_delay_v1.md`](exec_delay_v1.md) | 2026-10-14 | Fill-timing family (next close, open t+2, split, exits-open/entries-close): next-close D1 looks good on the main window (15.05%, DD 42.4%) but fails every real-open OOS window (−2.35 pp ^GDAXI, −1.60 pp ^N225); production next-open fills stay. Evidence: tag `archive/research/exec-delay-v1-2026-10`. | current evidence |
| [`moc_execution_v1.md`](moc_execution_v1.md) | 2026-10-14 | Same-close (market-on-close) fills are worse than next-open fills (13.73% vs 14.67%, DD 55.0%), also with pre-close signal noise; QQQ earns ~+9.8%/yr overnight vs −2.2%/yr intraday. Evidence: tag `archive/research/moc-execution-v1-2026-10`. | current evidence |
| [`seasonal_placebo_v1.md`](seasonal_placebo_v1.md) | 2026-10-14 | Placebo test of S3 (cash in September): not credible — post-1999 non-US rank 8/12, Calmar gain only at the 87th percentile of random-month placebos, and the whole 7.6 pp DD gain comes from September 2000. Evidence: tag `archive/research/seasonal-placebo-v1-2026-10`. | current evidence |
| [`seasonal_overlay_v1.md`](seasonal_overlay_v1.md) | 2026-10-14 | Calendar overlays on E2 (half exposure May–Oct, seasonal vol target, cash in September, turn-of-month): only S3 (cash in September) passed the gates (via Calmar: −0.29 pp CAGR, DD 42.0%), later judged not credible by `seasonal_placebo_v1`. Evidence: tag `archive/research/seasonal-overlay-v1-2026-10`. | superseded by `seasonal_placebo_v1` |
| [`credit_vote_v1.md`](credit_vote_v1.md) | 2026-10-14 | Credit-spread (BAA−10y) and yield-curve votes/vetoes on E2 all fail, losing 3–5 pp CAGR (veto) or deepening DD (credit replacing TSM). Evidence: tag `archive/research/credit-vote-v1-2026-10`. | current evidence |
| [`vol_window_v1.md`](vol_window_v1.md) | 2026-10-14 | Vol window 10/40/60, max(vol20, vol60) and downside semi-deviation instead of vol20 all fail (V10 in-sample +0.03 pp but OOS negative; V40 fails 2017+ and the bootstrap). Evidence: tag `archive/research/vol-window-v1-2026-10`. | current evidence |
| [`ewma_band_v1.md`](ewma_band_v1.md) | 2026-10-12 | No arm passes on top of production E2 (2000–2026, both financing models, ^GSPC 1957–99 + five OOS markets, two bootstraps): EWMA vol (λ 0.94) gains +0.04 pp CAGR and cuts max DD 49.6% → 47.4% but fails 2017+ (−1.13 pp) and the bootstrap (−0.07 pp); 2.5/5/10 pp resize bands cut sells/yr from ~27 to 6–14 but lose 0.07–0.60 pp CAGR and the bootstrap; production E2 unchanged, no Stage B. | current evidence |
| [`after_tax_tr_v1.md`](after_tax_tr_v1.md) | 2026-10-12 | Turkish-resident after-tax model (2026 GVK rules: TRY gains at CBRT rates, Yi-ÜFE indexation, progressive tariff, FIFO, liquidated at end): E2 still beats SPY by +2.4 to +3.6 pp/yr on 2000–2026 (pre-tax +6.1) and +9.8 to +12.0 pp on 2017–2026; tax drag 3.6–9.1 pp/yr vs SPY 1.2–2.7; QQQ beats E2 after tax since 2010 at $100k; ~90% of realised gain comes from vol-resize trims; wider bands (5/10 pp) don't help. Not tax advice. | current evidence |
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
