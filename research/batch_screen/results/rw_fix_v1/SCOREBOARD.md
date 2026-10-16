# batch rw_fix_v1 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 303 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC31/A` | **w20020731** | FAIL | reject_stage1 | - | -0.2287 | -0.9868 / 0.261 | -1.3527 / 0.192 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC31/B` | **w20020731** | FAIL | reject_stage1 | - | +0.2809 | -0.8362 / 0.245 | -1.0762 / 0.195 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC31/C` | **w20020731** | FAIL | reject_stage1 | - | -0.5326 | -1.0460 / 0.220 | -1.0913 / 0.199 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC32/A` | **w20070412** | FAIL | reject_stage1 | - | +0.4941 | -0.8314 / 0.343 | -1.1956 / 0.275 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC32/B` | **w20070412** | FAIL | reject_stage1 | - | -0.3123 | -0.9125 / 0.264 | -0.9724 / 0.237 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC32/C` | **w20070412** | FAIL | reject_stage1 | - | -0.0024 | -1.0930 / 0.259 | -1.0984 / 0.256 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC33/A` | **w20031208** | FAIL | reject_stage1 | - | -0.8846 | -0.9629 / 0.249 | -1.4010 / 0.148 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC33/B` | **w20031208** | FAIL | reject_stage1 | - | -0.3693 | -0.7253 / 0.230 | -1.0022 / 0.153 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC33/C` | **w20031208** | FAIL | reject_stage1 | - | -1.0972 | -1.0918 / 0.170 | -1.1500 / 0.151 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC34/A` | default | FAIL | reject_stage1 | - | -1.2542 | +0.0976 / 0.507 | -0.3723 / 0.456 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC34/B` | default | FAIL | reject_stage1 | - | -0.6369 | +0.3141 / 0.540 | -0.2330 / 0.463 | - | - | - | 0.0725 | rejected (stage 1) |
| `IC35/A` | **w20060523** | FAIL | reject_stage1 | - | +1.8302 | +1.0421 / 0.582 | +1.4072 / 0.607 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC35/B` | **w20060523** | FAIL | reject_stage1 | - | +1.5329 | +1.1354 / 0.642 | +1.1365 / 0.650 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC35/C` | **w20060523** | FAIL | reject_stage1 | - | -0.2802 | -0.5203 / 0.439 | -0.3056 / 0.464 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC37/A` | **w20030103** | FAIL | reject_stage1 | - | +0.0158 | -0.6379 / 0.333 | -0.8195 / 0.274 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC37/B` | **w20030103** | FAIL | reject_stage1 | - | +0.0599 | -0.7971 / 0.267 | -0.8907 / 0.228 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC37/C` | **w20030103** | FAIL | reject_stage1 | - | +0.5879 | -0.2363 / 0.385 | -0.3366 / 0.357 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC38/A` | default | FAIL | reject_stage1 | - | -2.1957 | +2.1019 / 0.681 | +1.8223 / 0.660 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC38/B` | default | FAIL | reject_stage1 | - | -0.9860 | +1.3638 / 0.736 | +1.1995 / 0.691 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC38/C` | default | FAIL | reject_stage1 | - | -1.8280 | +2.1975 / 0.717 | +1.9886 / 0.684 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC42/A` | default | FAIL | reject_stage1 | - | -1.3399 | -1.9798 / 0.001 | -1.1163 / 0.010 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC42/B` | default | FAIL | reject_stage1 | - | -1.3229 | -1.9785 / 0.001 | -1.1147 / 0.009 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC42/C` | default | FAIL | reject_stage1 | - | -1.3652 | -1.9813 / 0.001 | -1.1218 / 0.009 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC43/A` | default | FAIL | reject_stage1 | - | -1.4665 | -2.1200 / 0.002 | -1.2371 / 0.007 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC43/B` | default | FAIL | reject_stage1 | - | -1.6569 | -2.2045 / 0.002 | -1.2935 / 0.007 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC43/C` | default | FAIL | reject_stage1 | - | -1.4395 | -2.0878 / 0.001 | -1.2057 / 0.009 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC44/A` | default | FAIL | reject_stage1 | - | -1.3417 | -1.9674 / 0.001 | -1.1092 / 0.009 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC44/B` | default | FAIL | reject_stage1 | - | -1.3410 | -1.9177 / 0.002 | -1.0990 / 0.014 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC44/C` | default | FAIL | reject_stage1 | - | -1.3132 | -1.9588 / 0.001 | -1.0977 / 0.008 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC45/A` | **w20041119** | PASS | holdout_insufficient | G3_base, G3_calibrated, G4, G7, G8, AT_2010_01_to_end, AT_2017_01_to_end, E1, E2, HOLDOUT_INSUFFICIENT | +0.0070 | +0.4654 / 0.689 | +0.2739 / 0.597 | - | +0.0000 | PASS | 0.0000 | holdout insufficient |
| `IC45/B` | **w20041119** | FAIL | reject_stage1 | - | -0.1446 | +0.8626 / 0.678 | +0.4228 / 0.569 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC46/A` | **w20020731** | FAIL | reject_stage1 | - | -0.6593 | -0.2901 / 0.410 | -0.6203 / 0.305 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC46/B` | **w20020731** | FAIL | reject_stage1 | - | -1.6845 | -0.6088 / 0.376 | -1.3518 / 0.280 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib25_RW/A_RW` | **w20020731** | FAIL | reject_stage1 | - | -0.2669 | -0.8328 / 0.055 | -1.0530 / 0.017 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib25_RW/B_RW` | **w20020731** | FAIL | reject_stage1 | - | -0.2760 | -1.0808 / 0.020 | -1.1328 / 0.016 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib25_2L/A_2L` | default | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `ib25_2L/B_2L` | default | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IB26_RW/A_RW` | **w20020731** | FAIL | reject_stage1 | - | -0.1121 | -0.5282 / 0.258 | -0.6066 / 0.242 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB26_RW/B_RW` | **w20020731** | FAIL | reject_stage1 | - | -0.1438 | -0.2822 / 0.334 | -0.2856 / 0.315 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB26_RW/C_RW` | **w20020731** | FAIL | reject_stage1 | - | +0.1876 | -0.3101 / 0.288 | -0.3249 / 0.276 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB26_2L/A_2L` | default | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IB26_2L/B_2L` | default | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IB26_2L/C_2L` | default | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `ib27_2L/A_2L` | default | FAIL | reject_stage1 | - | -1.3164 | -1.7169 / 0.005 | -0.8650 / 0.030 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib27_2L/B_2L` | default | FAIL | reject_stage1 | - | -1.3305 | -1.9682 / 0.001 | -1.1056 / 0.010 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib27_2L/C_2L` | default | FAIL | reject_stage1 | - | -1.3833 | -1.7873 / 0.002 | -1.0467 / 0.007 | - | - | - | 1.0000 | rejected (stage 1) |

## manual confirmation

These arms came through stage 1 but their holdout is not evaluable (fewer than 3 applicable OOS windows or fewer than 15 applicable instruments): **not a pass**. Each needs its own pre-registered confirmation study on the windows its inputs reach.

* `IC45/A` — 0 applicable OOS windows, 0 applicable instruments; reasons: {"DIA": "the leg-B instrument 'gld' has no price on 1999-01-20 (the series covers 2004-11-18..2026-10-12 of this window)", "EEM": "the leg-B instrument 'gld' has no price on 2004-04-14 (the series covers 2004-11-18..2026-10-13 of this window)", "EFA": "the leg-B instrument 'gld' has no price on 2002-09-03 (the series covers 2004-11-18..2026-10-13 of this window)", "EWA": "the leg-B instrument 'gld' has no price on 1997-03-17 (the series covers 2004-11-18..2026-10-13 of this window)", "EWG": "the leg-B instrument 'gld' has no price on 1997-03-17 (the series covers 2004-11-18..2026-10-13 of this window)", "EWH": "the leg-B instrument 'gld' has no price on 1997-03-17 (the series covers 2004-11-18..2026-10-13 of this window)", "EWJ": "the leg-B instrument 'gld' has no price on 1997-03-17 (the series covers 2004-11-18..2026-10-13 of this window)", "EWL": "the leg-B instrument 'gld' has no price on 1997-03-17 (the series covers 2004-11-18..2026-10-13 of this window)", "EWS": "the leg-B instrument 'gld' has no price on 1997-03-17 (the series covers 2004-11-18..2026-10-13 of this window)", "EWU": "the leg-B instrument 'gld' has no price on 1997-03-17 (the series covers 2004-11-18..2026-10-13 of this window)", "GSPC_1957_1999": "the leg-B instrument 'gld' has no price on 1956-12-31 (the series covers empty in this window of this window)", "IWM": "the leg-B instrument 'gld' has no price on 2001-05-29 (the series covers 2004-11-18..2026-10-12 of this window)", "M1": "the leg-B instrument 'gld' has no price on 1974-12-31 (the series covers empty in this window of this window)", "M2": "the leg-B instrument 'gld' has no price on 1935-01-07 (the series covers empty in this window of this window)", "M3": "the leg-B instrument 'gld' has no price on 1966-01-18 (the series covers 2004-11-18..2026-09-29 of this window)", "M4": "the leg-B instrument 'gld' has no price on 1989-01-02 (the series covers 2004-11-18..2026-09-29 of this window)", "M5": "the leg-B instrument 'gld' has no price on 1984-12-31 (the series covers 2004-11-18..2026-09-29 of this window)", "SPY": "the leg-B instrument 'gld' has no price on 1994-01-27 (the series covers 2004-11-18..2026-10-12 of this window)", "XLB": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLE": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLF": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLI": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLK": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLP": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLU": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLV": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)", "XLY": "the leg-B instrument 'gld' has no price on 1999-12-22 (the series covers 2004-11-18..2026-10-13 of this window)"}

