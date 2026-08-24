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

## 4. Next Steps (Staged Commits)

**COMMIT A (this commit):** Audit tooling + provider probes + manifest, no new V3 performance result. This file is baseline only.

**COMMIT B:** Frozen generated dataset + identity/lifetime metadata + coverage report (`equity_survivorship_aware_2016_2026_v2.csv`, `equity_entity_lifetimes_v2.csv`, manifests). Old vs new coverage (symbols, entities, survivor ratio, yearly counts 2017-2026).

**COMMIT C:** Frozen V3 old-vs-new validation tooling (if coverage passes threshold).

**COMMIT D:** V3 results/report (only if dataset quality acceptable; else `SURVIVORSHIP RECONSTRUCTION NOT FEASIBLE`).

No `V4`, no `PAPER` change, `LIVE` disabled throughout.

---

## 5. Open Questions for Dataset Construction

* SIP entitlement for `SIP` feed vs `IEX` fallback — to be verified at runtime with explicit failure recording.
* `asof` mapping stability for `FB→META` etc — to be tested deterministically before mass download.
* Evidence classes `VERIFIED_TERMINATION` / `RENAMED` / `POSSIBLE_DELISTING` / `DATA_GAP` / `UNKNOWN` and tiers `A/B/C/D` — to be defined and counted.
* Terminal position behavior in `PortfolioBacktestEngine` when held symbol has no future bars — to be audited (price stale, liquidation, halt).
* Historical `tradable` not back-projected — scanner eligibility at date T must use historical inputs (`price`, `liquidity`, `ATR`, `completeness`) not current-state metadata.

*No claim of survivorship-bias-free dataset before coverage audit.*

