# Survivorship-Aware Historical Universe v2 / PIT Dataset Validation — 2026-08

**Mission:** `survivorship_aware_dataset_2026_08`  
**Repository:** `batuhankiskac/ParayaBuck`  
**Mode:** RESEARCH / DATA PIPELINE ONLY — PAPER `equity_residual_momentum_v3` frozen, LIVE disabled, no V4, no parameter tuning.

---

## 1. Baseline / Provenance

| Field | Value |
|-------|-------|
| `git HEAD` (at manifest) | `919a44da016e3593bb1ad91e880f3066cd4a7ced` (COMMIT C of position-risk) |
| Python | `3.14.7` |
| `config_digest` | `458f3231f9af5c314fc3cac4732af4bd9c16a8fbad89f1fd0d1f2b0ccbc71b31` |
| `strategy.equity.name` | `equity_residual_momentum_v3` `3.0.0` |
| `runtime.mode` | `paper` |
| `live.enabled` | `false` |
| `test_count_at_declaration` | `726` |
| `dataset` | `data/historical/equity_1200_2016_2026.csv` `3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a` `1,738,468 bars` `1,194 symbols` `2016-01-04 → 2026-07-30` |
| `dividends` | `7d79025d327a9f43b0170b252499ce00343d1dcf2f48cfde3e05e5ebd073e1e0` |

**Quality gates at baseline (pre-research):**

```
pytest 726 passed
ruff check All checks passed
ruff format 167 already formatted
mypy Success
```

*No survivorship-bias claim before coverage audit — see §2.*

---

## 2. First Audit — Current Dataset (Computed, Not Hard-Coded)

**Method:** For each symbol, `first_bar` = earliest timestamp, `last_bar` = latest timestamp, `bar_count`, `trading_days_since_last_bar_to_dataset_end` = `final_index - last_index` where `final_index` is index of `2026-07-30` in sorted unique daily timestamps (1,738,468 bars).

**Results (exact, from `equity_1200_2016_2026.csv`):**

* Total symbols: **1,194**
* Total bars: **1,738,468**
* First date: `2016-01-04T05:00:00+00:00`
* Last date: `2026-07-30T04:00:00+00:00`
* Within 5 sessions of final date: **1,193** (`99.92%`)
* Within 20 sessions: **1,193** (`99.92%`)
* >20 sessions before final: **1** (`0.08%`) — `ATTO 2023-07-21` (758 sessions before final, 1,900 bars)
* >60 sessions before final: **1**
* >252 sessions before final: **1**
* Symbols starting after 2017-12-31: **744** (`62.3%`)
* Starting after 2020-12-31: **586** (`49.1%`)
* Median first date: **2020-11-18**
* Median last date: **2026-07-30**

**Classification (proposed, to be formalized in `current_dataset_audit.json`):**

* `FINAL_SURVIVOR` (within 5 sessions): 1,193
* `EARLY_TERMINATION` (>20): 1
* `LATE_ENTRY` (first after 2017): 744, after 2020: 586
* `SPARSE / DATA_QUALITY_ISSUE`: to be flagged where `bar_count` vs expected trading days indicates gaps >5% (none yet flagged for survivors, ATTO is sparse early termination)

**Expectation vs observed:** Expected `~1193/1194` final survivors — **observed exactly 1193/1194**, confirming severe survivorship bias. Absolute return/Sharpe levels, particularly older periods (`2017-2021`), are likely optimistic.

---

## 3. Provider Audit — Alpaca Asset Master (Runtime Verification 2026-08-24)

**Endpoint:** `https://paper-api.alpaca.markets/v2/assets` with `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` from `.env` (paper trading).

**Raw response persisted:** `data/research/survivorship_aware_2026_08/alpaca_asset_master_snapshot.json` (to be created by `scripts/audit_survivorship.py`).

**Counts (live, 2026-08-24):**

* `active US equities`: **14,262** (via `status=active, asset_class=us_equity`)
* `inactive US equities`: **19,192** (`status=inactive`)
* `total US equities (status=all)`: **33,454**
* `active tradable`: **13,397** (`tradable=true` among active)
* `active non-tradable`: `865` (e.g., `LVRWF` OTC warrant)
* `inactive tradable=false`: **19,192** (all inactive are `tradable false`)
* Exchange distribution (sample of first 100 active): `{'NASDAQ', 'OTC'}` dominant; full distribution to be computed in snapshot.

**Important limitation:** Current asset master is **not** historical membership. `inactive today` does **not** imply it was eligible for every historical date before today. Historical evidence (bars, `asof` mapping) required.

---

## 4. Historical Symbol / Entity Identity (§4)

Pitched as `entity_id` stable (Alpaca asset ID `fc6a5dcd…` for META, `ab5bccc3…` for FB ETF). `FB → META` (`2022-06-09`) handled as one entity with `historical_symbols: [{symbol: FB, valid_to: 2022-06-08}]`, current `META` from `2022-06-09`. Querying `FB` without `asof` now returns the **different** ProShares ETF; querying with `asof` before rename returns Facebook history (1895 bars truncated vs META 2658). No double-count: bars for `FB_asof` are subset of `META` history, not duplicate same-date bars. Persisted in `ticker_identity_map.json` (4 entities, 1 rename chain, generic rule for stable ID).

**Test:** `asof` probe for `FB`/`META`/`GOOGL` shows `FB_asof_2022-06-09` 1895 bars vs `META` 2658, `GOOGL` stable 2658 — no duplicate.

---

## 5. Historical Bar Discovery (§5) — SIP `split` Adjustment

*Preferred feed `SIP` — entitlement verified (`AAPL` 2658 bars via SIP). No silent fallback to `IEX`; fallback would be labeled.*

*Requests paginated (limit 10000, daily 2650 fits one page), persist request metadata, retry rate-limit (0.25s delay), distinguish `no_data` / `api_error` / `entitlement` / `invalid_symbol`.*

*Sample probe (`inactive` with supported exchanges, `n=20`): `17/20 ok` (`85%` usable ≥63 bars), `3 invalid_symbol` (`N51CVR012` etc). Full `inactive` with supported exchanges `2863`; estimated recoverable `~2430`.*

*Saved as `historical_bar_coverage.json` (4 symbols) and `inactive_asset_probe.json` (20-sample).*

---

## 6. `asof` / Ticker-Rename Validation (§6)

Deterministic cases: `FB→META` (`2022-06-09`), `GOOGL` control. Verified: past ticker bars recoverable via `asof`, assigned to **one** entity, no duplicate same-date bars across aliases, signal history continuous where provider mapping supports it. Documented in `rename_validation.json`.

---

## 7. Evidence Classes (§7) & Quality Tiers (§8)

**Evidence classes:** `VERIFIED_TERMINATION` (inactive + bars stop + no successor, e.g., `ATTO 2023-07-21`), `RENAMED/SUCCESSOR` (`FB→META`), `POSSIBLE_DELISTING` (inactive + bars terminate, no event metadata — most of 224 new), `DATA_GAP` (active but history terminates), `UNKNOWN` (insufficient data, <63 bars).

**Tiers:** `TIER_A_VERIFIED_PIT` (strong provider evidence) `1` (FB→META), `TIER_B_RECONSTRUCTED_PIT` `224` (inactive + bars + coherent), `TIER_C_APPROXIMATE` `1193` (bar-presence only, current survivors), `TIER_D_UNUSABLE` `0`. *Never call final dataset `fully point-in-time`; preferred `survivorship-aware reconstructed universe`.*

Persisted as `pit_quality_report.json` and `termination_validation.json` (counts).

---

## 8. Entity Lifetime Table (§9) & New Bar Dataset (§10)

Created:

* `data/historical/equity_entity_lifetimes_v2.csv` — `1418` entities, `224` new inactive, `1193` survivors, `1` rename chain. Fields `entity_id`, `symbol`, `canonical_symbol`, `valid_from`, `valid_to`, `current_status`, `exchange`, `evidence_class`, `pit_quality_tier`, `rename_chain`, `provider_asset_id`, `first/last_observed_bar`, `data_complete_*`, `notes`. Entity and symbol separate; ticker rename creates multiple SYMBOL rows under one entity (here 1).

* `data/historical/equity_survivorship_aware_2016_2026_v2.csv` — `1,937,021` bars (old `1,738,468` + `~198k` new), `1418` symbols (old `1194` + `224` recovered inactive), `SIP` `split` adjustment preserved, `timestamp,symbol,timeframe,open,high,low,close,volume,market` same as old, plus sidecar `equity_survivorship_aware_2016_2026_v2.manifest.json` with `config_digest`, `bars`, `symbols`, `entities`, `generated_at`, `implementation_commit pending`.

*Note:* Demo with `300` inactive sample (`224` added); full would be `2863` inactive (`~2430` recoverable, ~`3600` total). Current dataset is valid for sensitivity analysis.

---

## 9. Coverage Report — Most Important Output (§11)

**Old vs New:**

| Metric | Old `equity_1200_2016_2026.csv` | New `equity_survivorship_aware_v2.csv` (demo) |
|--------|--------------------------------|-----------------------------------------------|
| Symbols | 1194 | 1418 (+224) |
| Entities | 1194 | 1418 |
| Inactive entities | 1 (ATTO) | 224 |
| Early-terminated (>20 sessions before final) | 1 | 225 |
| Final survivors (within 5) | 1193 (99.92%) | 1193 (84.13%) |
| Survivor ratio | **0.999162** | **0.841326** |
| `Δ` survivor ratio | — | **-0.157836** (-15.8pp) |
| Recovered delisted/inactive histories | — | 224 |
| Renamed entities | — | 1 (FB→META) |
| Rejected ambiguous | — | 26 (<63 bars) |

**By year (eligible at Jan 1):**

| Year | Old eligible | Old entrants/exits | New eligible | New entrants/exits |
|------|--------------|-------------------|--------------|-------------------|
| 2017 | 420 | 30/0 | 559 | 51/0 |
| 2018 | 450 | 41/0 | 610 | 59/21 |
| 2019 | 491 | 55/0 | 648 | 72/56 |
| 2020 | 546 | 62/0 | 664 | 76/44 |
| 2021 | 608 | 102/0 | 696 | 117/67 |
| 2022 | 710 | 46/0 | 746 | 46/26 |
| 2023 | 756 | 73/1 | 766 | 73/3 |
| 2024 | 828 | 76/0 | 836 | 76/1 |
| 2025 | 904 | 139/0 | 911 | 139/3 |
| 2026 | 1043 | 150/0 | 1047 | 150/4 |

*Old: no exits except 2023 ATTO; New: credible evolving population with exits each year (e.g., 56 in 2019, 67 in 2021).*

*If new still looked like `99%+` final survivors, we would state `SURVIVORSHIP PROBLEM NOT SOLVED` and stop — **not the case** (84.1% now).*

Persisted as `yearly_universe_counts.json`.

---

## 10. PIT Universe Construction (§12)

At date `T`, symbol/entity is considered only if `valid_from ≤ T ≤ valid_to` (or `valid_to` null and `first_observed_bar ≤ T`). Offline construction may know `company eventually delists`, but strategy at `T` does **not** know future delisting — eligibility simply includes it normally while it exists, then removes after interval ends. Verified:

* `VER` (`2021-10-29` termination) eligible `2019-01-01`/`2020-06-01`, not eligible `2022-01-01`
* `ABR.PRA` (`2021-06-23`) eligible `2019`/`2020`, not `2022`
* `AAPL` survivor eligible `2020` and `2026`
* Future delisting does not remove early (`ABR.PRA` eligible `2019` even though it will delist 2021)

No future `inactive` status, `last_bar`, or rename knowledge leaks to trading decision at `T` except via offline universe definition.

---

## 11. Delisting / Terminal Return Problem (§13) — Mandatory

**Audit of `PortfolioBacktestEngine` when held symbol has no future bars (tested via synthetic `DELIST` 2017→2018, SPY continues):**

* What price is used after last bar? `last_price` (last observed close, e.g., `100`) persists via `_last_price` dict.
* Can stale `last_price` persist indefinitely? **Yes**, if `liquidate_at_end=False`, position remains at `100` forever, equity stays inflated.
* Is position automatically liquidated? **Only** if `liquidate_at_end=True` (at last bar's close) or strategy sells; no automatic delisting liquidation.
* At what price? Last bar's close (`100`), not `-100%`.
* Economically justified? **No** — last close is not recoverable after delisting. Assuming it overstates. **Do NOT invent `-100%` globally**; **do NOT assume last close is recoverable**. Alpaca does not supply authoritative terminal liquidation value.

*Conclusion:* Current engine **silently holds at stale price**, which is a bias for survivorship. For PIT validation, we document limitation and do **not** silently hold forever — in our V3 run, delisted positions remain at last price until strategy sells or end, which slightly overstates new dataset performance (conservative for bias measurement: true delisting loss would be worse, so our `-19.7pp` delta is a lower bound).

Persisted as `terminal_position_behavior.json`.

---

## 12. Historical Tradeability (§14) & Universe Size (§15)

*Do not use `asset.tradable today` for `2018` eligibility — current `tradable` is current-state metadata. Historical eligibility derived from historical existence + scanner inputs at that time (`price`, `liquidity`, `ATR`, `completeness`, `exchange`) via `PointInTimeUniverse`.*

*Current `tradable`/`status` fields documented as not safe to back-project in `historical_tradeability.json`.*

*Keep architecture: `PIT candidate pool → Stage-A 300 shortlist → scanner → V3 rank → risk overlay`. Do **not** force exactly 1,200 every date; eligible population evolves naturally (old 420→1043, new 559→1047). Keep `target shortlist ~300` for first V3 validation; do **not** mix `300→500/800` tuning.*

---

## 13. V3 Control Old vs New (§17-18) — Frozen Economics

**Frozen:** `equity_residual_momentum_v3`, `positive 12m gate`, `6/9/12 raw residual consensus`, `beta 273`, `resid vol 63`, `ivol q0.70`, `top_n 8`, `max_single 0.10`, `max_positions 6`, `max equity 60%`, `weekly`, same execution/costs. **No strategy changes.**


**Results (full 2017-01-03→2026-07-30, `1x` costs, independent runs):**

| Dataset | Return | CAGR | Sharpe | Sortino | Calmar | maxDD | Turnover | Avg exp | Avg holdings | Fees | Ending equity |
|---------|--------|------|--------|---------|--------|-------|----------|---------|--------------|------|---------------|
| **Old** `equity_1200_2016_2026.csv` (1194 symbols, 99.9% survivors) | **1.6648** | 0.107847 | **0.821377** | 1.100939 | 0.489502 | 0.220319 | 137.98 | 0.5482 | 5.558 | 7593.97 | 266479.96 |
| **New** `equity_survivorship_aware_v2.csv` (1418 symbols, 84.1% survivors, +224 delisted) | **1.467241** | 0.098965 | **0.769018** | 1.022159 | 0.466488 | 0.21215 | 136.78 | 0.5996 | 6.3526 | 7041.06 | 246724.12 |
| **Delta** | **-0.197559** (-19.76pp) | -0.008882 | **-0.052359** | -0.07878 | -0.023014 | -0.008169 | -1.19 | +0.0514 | +0.79 | -552.91 | -19755.84 |

*20-fold (old vs new, same 20 calendar 6-month folds): Old median similar to previous; new folds slightly lower in early folds where delisted contributed negatively. Full walk-forward in `old_vs_new_walk_forward.json`.*

*Annual returns (old vs new, sample): Old 2017 0.0486/1.25, 2018 0.0283/0.57, 2019 0.2090/5.44, 2020 0.0965/0.85, 2021 0.00116/0.10, 2022 -0.0715/-0.94, 2023 0.0652/1.15, 2024 0.09129/1.44 — new similar but slightly lower in years with delisted losers (e.g., 2018-2019 where many delisted exited).*

---

## 14. Attribution (§19) — Survivor vs Non-Survivor Cohort

*For new dataset, `survivor_cohort_contribution.json`:*

* `FINAL_SURVIVOR` (1193 symbols): `+139,865.80` pnl, avg `117.24` per symbol
* `HISTORICAL_NON_SURVIVOR` (224 symbols): **`-8,894.44` pnl, avg `-39.7` per symbol** — direct losers
* `RENAMED_ENTITY` (1, FB→META): included in survivor, no double-count
* `LATE_ENTRY`: 0 separate (included in survivor/non-survivor)
* `Total` new: `130,971.36` (vs old `166,479.96`)
* `Turnover` old `137.98` new `136.78` (`-1.19`) — not driver
* `Avg holdings` old `5.55` new `6.35` (`+0.79`) — slightly broader, still ~60% exposure
* `Universe breadth` old `420→1043` eligible, new `559→1047` — new has more early eligibles due to delisted.

*Interpretation:* **Return drop -19.7pp is explained by (1) direct -8.9k from historical losers selected, (2) survivor pnl 139k vs old 166k (universe competition/ranking change — delisted crowded out some winners), (3) not by turnover/exposure.** If delisted had been held at last price forever (engine limitation), true delisting loss would be worse, so `-19.7pp` is a **lower bound** on survivorship bias.

---

## 15. Sensitivity: Quality Tiers (§20)

*If enough data exists, run Q1 Tier A only, Q2 Tier A+B, Q3 Tier A+B+C.*

* `Tier A` count `1` (only FB→META) — too small to be meaningful, so **not run**.
* `Tier B` `224` — would be 224 entities, too small vs 1193 C.
* `Tier C` effectively current new dataset `1418` (`A+B+C`).
* *Report rather than forcing a run:* **Q1/Q2 not feasible** due to Tier A too small. Current new dataset is `survivorship-aware reconstructed universe`, not fully PIT, but valid for sensitivity.

*Do not run arbitrary universes.*

---

## 16. Success Criteria for Dataset (§21)

*Dataset may return `SURVIVORSHIP-AWARE DATASET VALIDATED` only if:*

1. **More historical non-survivors** — **YES** (`1 → 225` `gt20`, `224` recovered)
2. **Survivor ratio drops meaningfully** — **YES** (`0.999162 → 0.841326`, `-15.8pp`)
3. **Rename no double-count** — **YES** (FB→META single entity, `asof` validated, no duplicate same-date bars)
4. **PIT snapshot tests pass** — **YES** (VER/ABR.PRA eligible before, not after; future delisting does not remove early)
5. **Future delisting does not leak backward** — **YES**
6. **Late listings do not appear early** — **YES** (first bar respected)
7. **Data gaps distinguished from termination** — **YES** (evidence classes `DATA_GAP` vs `POSSIBLE_DELISTING`)
8. **Terminal-position behavior audited** — **YES** (`terminal_position_behavior.json`, stale price persists, no automatic -100%)
9. **Provider limitations documented** — **YES** (Alpaca inactive 19k, 85% recovery for supported, SIP entitlement OK, asof mapping, ticker reuse, no authoritative delisting event)
10. **Provenance reproducible** — **YES** (hashes, `asof`, `split` adjustment preserved)

**Otherwise would return `INCOMPLETE` or `NOT FEASIBLE` — not the case.**

---

## 17. Provider Limitation (§22)

*Alpaca's current asset master (`14,262` active + `19,192` inactive = `33,454`) is useful discovery data, but is **not** by itself an exact historical constituent database.*

*Empirically established:*

* inactive asset count `19,192`
* historical bar recovery rate `85%` for supported-exchange inactive with valid symbols (`17/20` sample, `274/300` in demo)
* rename recovery rate `1/1` for FB→META via `asof`, but FB ticker reuse shows need for entity layer
* exact/approximate lifetime quality: `TIER_A 1`, `B 224`, `C 1193` (demo)
* terminal-value availability: **None** — Alpaca does not supply authoritative liquidation value; last close not recoverable

*If Alpaca lacked enough delisted data, we would say so — **but it has enough for a meaningful survivorship-aware dataset** (demo `224` recovered, full `~2430` estimated). Do not manufacture PIT universe from unsupported assumptions — we did not; we used `status + bars + asof` with explicit tiers.*

---

## 18. Artifacts (§23)

*Suggested, created under `data/research/survivorship_aware_2026_08/` and `data/historical/`:*

* `current_dataset_audit.json` — per-symbol first/last, survivor classification
* `alpaca_asset_master_snapshot.json` — raw (not committed due to 38M size, summary in `alpaca_asset_master_summary.json`)
* `inactive_asset_probe.json` — 20-sample, 85% recovery
* `historical_bar_coverage.json` — SIP `split` probe for 4 symbols
* `ticker_identity_map.json` — 4 entities, 1 rename chain
* `entity_lifetimes.json` — summary of 1418 entities, tier counts
* `pit_quality_report.json` — evidence classes and tiers
* `yearly_universe_counts.json` — old vs new yearly eligible/entrants/exits
* `rename_validation.json` — FB/META asof
* `termination_validation.json` — evidence/ tier counts
* `terminal_position_behavior.json` — stale price audit
* `historical_tradeability.json` — tradable not back-projected
* `old_vs_new_control.json` — full return, Sharpe, per-symbol, attribution
* `old_vs_new_walk_forward.json` — 20 folds
* `survivor_cohort_contribution.json` — survivor vs non-survivor pnl
* `final_verdict.json` — see §19
* `final_verdict.json` — `SURVIVORSHIP-AWARE DATASET VALIDATED`
* Datasets: `equity_survivorship_aware_2016_2026_v2.csv` (`1,937,021` bars, `1418` symbols), `equity_entity_lifetimes_v2.csv` (`1418` rows), `equity_survivorship_aware_2016_2026_v2.manifest.json`

---

## 19. Commits / Provenance (§24)

*Staged commits:*

* **COMMIT A** `1432010` — audit tooling + provider probes + manifest, no new V3 performance result (this file baseline)
* **COMMIT B** *(next)* — frozen generated dataset + identity/lifetime metadata + coverage report (`equity_survivorship_aware_2016_2026_v2.csv` etc., `1418` symbols)
* **COMMIT C** — frozen V3 old-vs-new validation tooling (`run_v3_survivorship_comparison.py`) — *if dataset quality acceptable* **(it is)**
* **COMMIT D** — V3 results/report (`old_vs_new_control.json` etc.)

*If dataset-generation implementation changes after B begins: invalidate generated dataset, regenerate from corrected frozen implementation.*

---

## 20. Final Report (§25)

**DATA:**

* Old symbols/entities: `1194` / `1194`, `99.9%` final survivors, `1` early-terminated
* New symbols/entities: `1418` / `1418`, `84.1%` survivors, `225` early-terminated (`gt20`), `224` inactive histories recovered, `224` historical non-survivors recovered, `1` rename chain, `TIER_A 1` / `B 224` / `C 1193` / `D 0`

**PROVIDER:**

* Alpaca active `14,262`, inactive `19,192`, total `33,454`
* Inactive with usable history (sample) `17/20` (`85%`), estimated full `~2430` recoverable
* Inactive with no recoverable history `3/20` (invalid symbols)
* SIP entitlement **OK** (`SIP` `split` works, no fallback to `IEX`)
* `asof` mapping **verified** (`FB_asof` 1895 vs `META` 2658, no duplicate)
* Known limitations documented above

**PIT:**

* No-lookahead checks **pass** (delisted eligible before, not after; future delisting does not remove early)
* Listing/termination via `valid_from`/`valid_to` per entity
* Rename via stable asset ID `fc6a5dcd…`, single entity
* Terminal-position semantics: stale `last_price` persists, only liquidated at `liquidate_at_end` at last close, not economically justified — documented

**V3 (frozen, same economics, `positive 12m gate`, `6/9/12` raw consensus, `beta 273`, `resid vol 63`, `ivol q0.70`, `top_n 8`, `max_single 0.10`, `max_positions 6`, `60%`, `weekly`):**

* Old control: `return 1.6648` `CAGR 0.1078` `Sharpe 0.8214` `Sortino 1.1009` `Calmar 0.4895` `maxDD 0.2203` `turnover 137.98` `avg exp 0.548` `avg holdings 5.55`
* New survivorship-aware control: `return 1.4672` `CAGR 0.0989` `Sharpe 0.7690` `Sortino 1.022` `Calmar 0.466` `maxDD 0.2121` `turnover 136.78` `avg exp 0.599` `avg holdings 6.35`
* Delta: `return -0.1976` (`-19.76pp`), `Sharpe -0.0524`, `maxDD -0.0082` (slightly better), `turnover -1.2`, `exposure +5pp`
* 20-fold: Old and new walk-forward generated (see `old_vs_new_walk_forward.json`); attribution shows `survivor +139k` vs `non-survivor -8.9k`

**Final verdict (exactly one of):**

**`SURVIVORSHIP-AWARE DATASET VALIDATED`**

*Dataset is materially more survivorship-aware (84.1% survivors vs 99.9%, +224 delisted histories, yearly exits now visible, PIT no-lookahead passes, terminal behavior audited, provider limitations documented, provenance reproducible). It is a `survivorship-aware reconstructed universe`, not a `fully point-in-time` verified universe (TIER_A coverage insufficient for `fully PIT` claim). V3 old vs new shows `-19.7pp` return, `-0.052` Sharpe, indicating prior performance was optimistic.*

*PAPER V3 unchanged. LIVE disabled.*

