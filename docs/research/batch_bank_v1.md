# Batch screen of the idea bank, batch 1 (batch_bank_v1, 2026-10-16)

> Evidence: framework, plugins, ledger and per-arm results are not on `main`; they are preserved in
> tag `archive/research/batch-bank-v1-2026-10` (`research/batch_screen/`, `research/ideas/`,
> `research/batch_screen/results/bank_v1/`). Framework: `batch_screen_v1.md`. Ideas and their
> fixed arms: `idea_bank_v1.md` / `idea_bank_v1.json`.

**Verdict: no candidate.** 29 of the 40 idea-bank ideas fit the plugin model and were implemented
by four workers who never saw a result (plugins committed before the screen; the runner refuses
uncommitted code). 89 arms were simulated against production K; the trial ledger counts N = 107
distinct arms (incl. the anchors and the 11 arms declared as needing a new execution kernel:
IB25, IB26, IB27, IB35-C, IB38 and the five kernel ideas IB28/36/37/39/40 not yet registered).

| stage | arms |
|---|---|
| Stage 1 (main window 2000–2026 + its bootstrap): pass | 5 (IB08-C, IB10-B, IB10-C, IB31-C, ib32-A) |
| Stage 2 (held-out: 6 OOS windows, 21 ETFs, after-tax 2010/2017, fresh-seed bootstraps, rolling starts, pooled OOS bootstrap): pass | 0 |
| Declared after the multiple-testing deflation | 0 |

The five Stage-1 survivors all failed the held-out evidence: IB10-C (ATR-based vol estimator,
blend arm) came closest — it fails only the 2010/2017 after-tax windows and the rolling starts;
ib32-A (sticky vol dead-band) and IB10-B fail the pre-tax bootstrap, B2 and the fresh seeds;
IB08-C fails OOS, ETFs and the after-tax windows; IB31-C is identical to K on almost every
session. Wall clock of the whole batch: 8 min 47 s on 8 processes (≈ 13–20 s per arm; a few arms
with heavy signal code 1–5 min).

Scoreboard (Δ = arm − K, pp):

| arm | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `ib01/A` | FAIL | reject_stage1 | - | -1.8981 | -0.3655 / 0.435 | -0.2561 / 0.455 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib01/B` | FAIL | reject_stage1 | - | -3.0064 | -0.7220 / 0.387 | -0.6694 / 0.386 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib01/C` | FAIL | reject_stage1 | - | -0.1180 | -0.1695 / 0.409 | -0.1757 / 0.403 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB02/A` | FAIL | reject_stage1 | - | +0.1361 | -0.1115 / 0.463 | -0.0720 / 0.471 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB02/B` | FAIL | reject_stage1 | - | -3.3110 | -0.5910 / 0.420 | -0.5205 / 0.435 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB02/C` | FAIL | reject_stage1 | - | -0.0142 | -0.2495 / 0.438 | -0.2524 / 0.447 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib03/A` | FAIL | reject_stage1 | - | -1.3051 | -0.5844 / 0.423 | -0.4666 / 0.429 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib03/B` | FAIL | reject_stage1 | - | -2.3686 | -0.9181 / 0.387 | -0.7424 / 0.406 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib03/C` | FAIL | reject_stage1 | - | -0.4656 | -0.1015 / 0.443 | -0.0968 / 0.445 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB04/A` | FAIL | reject_stage1 | - | -3.8545 | -0.4638 / 0.427 | -0.4147 / 0.435 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB04/B` | FAIL | reject_stage1 | - | -3.5010 | -0.5013 / 0.425 | -0.4808 / 0.425 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB04/C` | FAIL | reject_stage1 | - | -11.2937 | -3.7665 / 0.329 | -3.9682 / 0.324 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib05/A` | FAIL | reject_stage1 | - | -0.9010 | -1.0315 / 0.286 | -0.5469 / 0.365 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib05/B` | FAIL | reject_stage1 | - | -3.9107 | -2.1899 / 0.261 | -1.4870 / 0.329 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib05/C` | FAIL | reject_stage1 | - | +0.0759 | -0.0000 / 0.470 | -0.0000 / 0.465 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB06/A` | FAIL | reject_stage1 | - | -0.7339 | -0.2813 / 0.352 | -0.2702 / 0.353 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB06/B` | FAIL | reject_stage1 | - | -0.6611 | -0.4940 / 0.354 | -0.4891 / 0.349 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB06/C` | FAIL | reject_stage1 | - | -6.2293 | -2.9469 / 0.232 | -2.2259 / 0.291 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib07/A` | FAIL | reject_stage1 | - | -0.2166 | -0.3567 / 0.383 | -0.0652 / 0.479 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib07/B` | FAIL | reject_stage1 | - | -0.2470 | -0.0244 / 0.480 | +0.0777 / 0.571 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib07/C` | FAIL | reject_stage1 | - | -0.0368 | -0.1190 / 0.417 | -0.0017 / 0.497 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB08/A` | FAIL | reject_stage1 | - | +0.2401 | +0.1080 / 0.547 | +0.2072 / 0.596 | - | - | - | 0.5000 | rejected (stage 1) |
| `IB08/B` | FAIL | reject_stage1 | - | +0.2260 | +0.0668 / 0.523 | +0.2834 / 0.594 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB08/C` | PASS | reject_stage2 | G4, G7, G8, AT_2010_01_to_end, AT_2017_01_to_end, B1_recent, E2, FRESH | +0.0237 | +0.0525 / 0.537 | +0.1412 / 0.590 | -0.3307 | +0.0517 | FAIL | 1.0000 | rejected (stage 2) |
| `ib09/A` | FAIL | reject_stage1 | - | -3.3690 | -0.3628 / 0.428 | -0.0583 / 0.490 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib09/B` | FAIL | reject_stage1 | - | -1.9216 | -0.1694 / 0.446 | -0.0207 / 0.490 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib09/C` | FAIL | reject_stage1 | - | -3.2587 | -0.3176 / 0.434 | +0.0138 / 0.504 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB10/A` | FAIL | reject_stage1 | - | +0.4886 | -0.0868 / 0.474 | -0.1763 / 0.448 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB10/B` | PASS | reject_stage2 | G5_base, G5_calibrated, B1_recent, B2_flat, FRESH | +0.3960 | +0.0411 / 0.530 | -0.0266 / 0.485 | +0.4369 | +0.1410 | FAIL | 0.0000 | rejected (stage 2) |
| `IB10/C` | PASS | reject_stage2 | AT_2010_01_to_end, AT_2017_01_to_end, E2 | +0.0449 | +0.0760 / 0.651 | +0.0688 / 0.632 | +0.0000 | +0.0539 | PASS | 0.0000 | rejected (stage 2) |
| `ib11/A` | FAIL | reject_stage1 | - | -1.5992 | -0.2412 / 0.376 | -0.2407 / 0.393 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib11/B` | FAIL | reject_stage1 | - | -0.2345 | -0.0289 / 0.460 | -0.0522 / 0.424 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib11/C` | FAIL | reject_stage1 | - | -0.0723 | -0.0322 / 0.411 | -0.0311 / 0.416 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB12/A` | FAIL | reject_stage1 | - | -2.1283 | -1.5805 / 0.369 | -1.0910 / 0.413 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB12/B` | FAIL | reject_stage1 | - | -2.1598 | -0.9459 / 0.358 | -0.6495 / 0.411 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB12/C` | FAIL | reject_stage1 | - | -0.9907 | -0.3372 / 0.426 | -0.3179 / 0.431 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib13/A` | FAIL | reject_stage1 | - | -3.3872 | -0.8249 / 0.435 | -0.2111 / 0.486 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib13/B` | FAIL | reject_stage1 | - | -1.9897 | -0.2321 / 0.469 | +0.3093 / 0.553 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib13/C` | FAIL | reject_stage1 | - | -0.7623 | -0.1394 / 0.474 | -0.0044 / 0.498 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB14/A` | FAIL | reject_stage1 | - | -0.2887 | -2.3055 / 0.325 | -1.7322 / 0.369 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB14/B` | FAIL | reject_stage1 | - | -0.8332 | -1.1454 / 0.345 | -0.7359 / 0.400 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB14/C` | FAIL | reject_stage1 | - | -0.3224 | -0.4401 / 0.432 | -0.3380 / 0.445 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib15/A` | FAIL | reject_stage1 | - | -3.5836 | -1.2800 / 0.337 | -0.4332 / 0.439 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib15/B` | FAIL | reject_stage1 | - | -1.2346 | -0.0514 / 0.476 | +0.3412 / 0.614 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib15/C` | FAIL | reject_stage1 | - | -2.1000 | -0.3280 / 0.446 | -0.1402 / 0.483 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB16/A` | FAIL | reject_stage1 | - | -0.5635 | -0.5878 / 0.241 | -0.3139 / 0.353 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB16/B` | FAIL | reject_stage1 | - | -0.3429 | -0.2470 / 0.201 | -0.1017 / 0.361 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB16/C` | FAIL | reject_stage1 | - | -1.3157 | -0.9834 / 0.048 | -0.3825 / 0.196 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib17/A` | FAIL | reject_stage1 | - | -2.7046 | -1.5359 / 0.112 | -0.5707 / 0.297 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib17/B` | FAIL | reject_stage1 | - | -4.1295 | -2.3208 / 0.132 | -1.0167 / 0.305 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib17/C` | FAIL | reject_stage1 | - | -2.8589 | -1.7391 / 0.106 | -0.8402 / 0.228 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB18/A` | FAIL | reject_stage1 | - | -2.2953 | -1.1187 / 0.199 | -0.2054 / 0.436 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB18/B` | FAIL | reject_stage1 | - | -0.9416 | -0.5802 / 0.201 | -0.2757 / 0.318 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB18/C` | FAIL | reject_stage1 | - | -1.8827 | -1.1762 / 0.179 | -0.7498 / 0.263 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib19/A` | FAIL | reject_stage1 | - | -0.7632 | -0.1021 / 0.466 | -0.1298 / 0.452 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib19/B` | FAIL | reject_stage1 | - | -0.0480 | +0.0435 / 0.524 | -0.0186 / 0.489 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib19/C` | FAIL | reject_stage1 | - | -0.3790 | +0.0208 / 0.512 | -0.0726 / 0.462 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB20/A` | FAIL | reject_stage1 | - | -1.6168 | -0.5593 / 0.407 | -0.4989 / 0.420 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB20/B` | FAIL | reject_stage1 | - | +0.4699 | -2.6643 / 0.316 | -2.1577 / 0.356 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB20/C` | FAIL | reject_stage1 | - | -0.2599 | -0.1179 / 0.424 | -0.1222 / 0.411 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib21/A` | FAIL | reject_stage1 | - | -2.4919 | -1.5124 / 0.277 | -1.3009 / 0.302 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib21/B` | FAIL | reject_stage1 | - | -0.4859 | -0.6118 / 0.307 | -0.4861 / 0.338 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib21/C` | FAIL | reject_stage1 | - | -0.8021 | -0.2257 / 0.375 | -0.2639 / 0.369 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB22/A` | FAIL | reject_stage1 | - | +0.0024 | -0.0539 / 0.438 | -0.0433 / 0.440 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB22/B` | FAIL | reject_stage1 | - | -0.1942 | -0.0480 / 0.434 | -0.0403 / 0.440 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB22/C` | FAIL | reject_stage1 | - | -0.4100 | -0.9761 / 0.124 | -0.3687 / 0.313 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib23/A` | FAIL | reject_stage1 | - | -3.6372 | -1.0875 / 0.337 | -1.0351 / 0.353 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib23/B` | FAIL | reject_stage1 | - | -0.2601 | -0.2375 / 0.357 | -0.2284 / 0.363 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib23/C` | FAIL | reject_stage1 | - | -1.5693 | -0.4004 / 0.284 | -0.3462 / 0.311 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB29/A` | FAIL | reject_stage1 | - | -1.9224 | -0.8155 / 0.347 | -0.7823 / 0.343 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB29/B` | FAIL | reject_stage1 | - | -2.7709 | -0.9349 / 0.356 | -1.0565 / 0.332 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB29/C` | FAIL | reject_stage1 | - | +0.1732 | -0.3809 / 0.393 | -0.2084 / 0.437 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib30/A` | FAIL | reject_stage1 | - | -2.2947 | -0.8595 / 0.361 | -1.0185 / 0.338 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib30/B` | FAIL | reject_stage1 | - | -2.4967 | -0.8886 / 0.369 | -1.1396 / 0.347 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib30/C` | FAIL | reject_stage1 | - | +0.8410 | -0.0082 / 0.495 | +0.3275 / 0.592 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB31/A` | FAIL | reject_stage1 | - | -1.4175 | +0.6625 / 0.731 | +0.6343 / 0.729 | - | - | - | 0.0000 | rejected (stage 1) |
| `IB31/B` | FAIL | reject_stage1 | - | -1.6602 | +0.6959 / 0.749 | +0.6678 / 0.742 | - | - | - | 0.0000 | rejected (stage 1) |
| `IB31/C` | PASS | reject_stage2 | G1_base, G5_base, G6_calibrated, AT_2010_01_to_end, AT_2017_01_to_end, B1_recent, B2_flat, E1, FRESH | +0.0000 | +0.0000 / 0.509 | +0.0000 / 0.510 | +0.0000 | -0.0000 | FAIL | 1.0000 | rejected (stage 2) |
| `ib32/A` | PASS | reject_stage2 | G5_base, G5_calibrated, G7, G8, B2_flat, FRESH | +0.5190 | +0.1172 / 0.586 | +0.0245 / 0.513 | -0.0212 | +0.1406 | FAIL | 0.0000 | rejected (stage 2) |
| `ib32/B` | FAIL | reject_stage1 | - | -0.3352 | -0.0452 / 0.483 | -0.1634 / 0.429 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib32/C` | FAIL | reject_stage1 | - | -0.2854 | +0.0296 / 0.553 | -0.0117 / 0.481 | - | - | - | 0.0245 | rejected (stage 1) |
| `IB33/A` | FAIL | reject_stage1 | - | -0.9225 | -0.1048 / 0.427 | -0.1106 / 0.446 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB33/B` | FAIL | reject_stage1 | - | -0.2506 | -0.0000 / 0.485 | +0.0000 / 0.505 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB33/C` | FAIL | reject_stage1 | - | -1.1858 | -0.3297 / 0.439 | -0.3397 / 0.438 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib34/A` | FAIL | reject_stage1 | - | -6.2509 | -1.9580 / 0.365 | -1.9153 / 0.374 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib34/B` | FAIL | reject_stage1 | - | -3.4444 | -1.1199 / 0.379 | -0.9818 / 0.409 | - | - | - | 1.0000 | rejected (stage 1) |
| `ib34/C` | FAIL | reject_stage1 | - | -0.4147 | -0.3114 / 0.371 | -0.3046 / 0.370 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB35/A` | FAIL | reject_stage1 | - | +0.0507 | -0.1892 / 0.443 | -0.2901 / 0.420 | - | - | - | 1.0000 | rejected (stage 1) |
| `IB35/B` | FAIL | reject_stage1 | - | +0.0524 | -0.6760 / 0.386 | -0.8079 / 0.355 | - | - | - | 1.0000 | rejected (stage 1) |

