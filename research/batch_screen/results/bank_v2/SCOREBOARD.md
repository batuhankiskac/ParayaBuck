# batch bank_v2 -- SCOREBOARD

control: `k_control/K` (production K). Stage 1: main window 2000-03-09 -> 2026-09-29 (QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap (seed 20261011, block 20, 5000 pre-tax / 1000 after-tax paths). Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, fresh-seed bootstraps seed 20261016 blocks 10/40, 21 January rolling starts, pooled OOS bootstrap). Δ = arm − K in pp.

registered trials N = 215 (`research/batch_screen/REGISTRY.jsonl`; N counts distinct `(idea, arm, spec_hash)`, the deflation's multiple-testing count).

| arm | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC01/A` | FAIL | reject_stage1 | - | -2.1335 | -0.6901 / 0.379 | -0.6305 / 0.384 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC01/B` | FAIL | reject_stage1 | - | -3.2270 | -0.7220 / 0.388 | -0.6623 / 0.393 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC01/C` | FAIL | reject_stage1 | - | -2.2045 | -0.8789 / 0.360 | -0.8957 / 0.361 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC02/A` | FAIL | reject_stage1 | - | -0.6986 | +0.0287 / 0.531 | +0.0202 / 0.521 | - | - | - | 0.8945 | rejected (stage 1) |
| `IC02/B` | FAIL | reject_stage1 | - | -0.7203 | -0.0131 / 0.494 | -0.0451 / 0.493 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC02/C` | FAIL | reject_stage1 | - | -0.5159 | -0.4133 / 0.344 | -0.3839 / 0.345 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC03/A` | FAIL | reject_stage1 | - | -2.7378 | -0.6613 / 0.395 | -0.5186 / 0.414 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC03/B` | FAIL | reject_stage1 | - | -2.4929 | -0.9507 / 0.347 | -0.8261 / 0.363 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC03/C` | FAIL | reject_stage1 | - | -3.8425 | -1.1153 / 0.342 | -0.9800 / 0.369 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC04/A` | FAIL | reject_stage1 | - | -3.3061 | -0.0138 / 0.496 | -0.0165 / 0.498 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC04/B` | FAIL | reject_stage1 | - | -3.0064 | -0.6970 / 0.388 | -0.6291 / 0.391 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC04/C` | FAIL | reject_stage1 | - | -0.2747 | -0.2056 / 0.382 | -0.2023 / 0.383 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC05/A` | FAIL | reject_stage1 | - | -5.0584 | -2.1505 / 0.304 | -1.7778 / 0.351 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC05/B` | FAIL | reject_stage1 | - | -6.0597 | -2.4100 / 0.308 | -2.0985 / 0.341 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC05/C` | FAIL | reject_stage1 | - | +0.0062 | -0.1583 / 0.415 | -0.1669 / 0.407 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC06/A` | FAIL | reject_stage1 | - | -0.5500 | +0.1594 / 0.543 | +0.1757 / 0.545 | - | - | - | 0.0095 | rejected (stage 1) |
| `IC06/B` | FAIL | reject_stage1 | - | -0.6118 | -0.2408 / 0.464 | -0.1564 / 0.466 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC06/C` | FAIL | reject_stage1 | - | -0.5000 | -0.0000 / 0.484 | -0.0000 / 0.485 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC07/A` | FAIL | reject_stage1 | - | -1.3000 | -0.2720 / 0.447 | -0.2193 / 0.464 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC07/B` | FAIL | reject_stage1 | - | -0.7238 | -0.1431 / 0.488 | -0.0545 / 0.494 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC07/C` | FAIL | reject_stage1 | - | -0.8239 | -0.4599 / 0.353 | -0.4118 / 0.354 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC08/A` | FAIL | reject_stage1 | - | -1.2988 | -0.6817 / 0.274 | -0.2406 / 0.417 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC08/B` | FAIL | reject_stage1 | - | -0.1375 | -0.1880 / 0.423 | -0.3305 / 0.359 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC08/C` | FAIL | reject_stage1 | - | -0.9336 | -0.3660 / 0.351 | -0.1435 / 0.442 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC09/A` | FAIL | reject_stage1 | - | -0.7432 | -0.1298 / 0.452 | +0.0020 / 0.501 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC09/B` | FAIL | reject_stage1 | - | -0.7115 | -0.4163 / 0.303 | -0.3809 / 0.326 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC09/C` | FAIL | reject_stage1 | - | -0.7612 | -0.1021 / 0.465 | +0.0402 / 0.520 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC10/A` | FAIL | reject_stage1 | - | -0.3367 | -1.1476 / 0.351 | -1.2803 / 0.341 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC10/B` | FAIL | reject_stage1 | - | +0.1824 | -0.2117 / 0.435 | -0.2994 / 0.392 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC10/C` | FAIL | reject_stage1 | - | -0.0526 | -0.0046 / 0.454 | -0.0050 / 0.454 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC11/A` | FAIL | reject_stage1 | - | -7.8446 | -2.4634 / 0.348 | -3.0972 / 0.319 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC11/B` | FAIL | reject_stage1 | - | -6.3234 | -1.8059 / 0.365 | -2.4941 / 0.332 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC11/C` | FAIL | reject_stage1 | - | -7.8446 | -2.4634 / 0.348 | -3.0972 / 0.319 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC12/A` | PASS | reject_stage2 | AT_2017_01_to_end, E2 | +0.7078 | +1.4136 / 0.633 | +2.1393 / 0.709 | +1.6800 | +2.2154 | PASS | 0.0000 | rejected (stage 2) |
| `IC12/B` | PASS | reject_stage2 | AT_2017_01_to_end | +0.6997 | +0.8519 / 0.647 | +1.2425 / 0.734 | +0.9734 | +1.3390 | PASS | 0.0000 | rejected (stage 2) |
| `IC12/C` | FAIL | reject_stage1 | - | -1.4244 | -0.6687 / 0.390 | -0.5826 / 0.400 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC13/A` | FAIL | reject_stage1 | - | -1.0228 | -0.6519 / 0.392 | -0.4666 / 0.424 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC13/B` | FAIL | reject_stage1 | - | -0.4556 | -0.2561 / 0.424 | -0.1553 / 0.445 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC13/C` | FAIL | reject_stage1 | - | -0.1639 | -0.0334 / 0.469 | -0.0176 / 0.487 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC14/A` | FAIL | reject_stage1 | - | -1.2305 | +0.2363 / 0.546 | +0.5857 / 0.616 | - | - | - | 0.8565 | rejected (stage 1) |
| `IC14/B` | FAIL | reject_stage1 | - | -1.5346 | -0.7916 / 0.384 | -0.6761 / 0.403 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC14/C` | FAIL | reject_stage1 | - | -0.6313 | +0.2373 / 0.544 | +0.2622 / 0.560 | - | - | - | 0.0145 | rejected (stage 1) |
| `IC15/A` | FAIL | reject_stage1 | - | -2.3329 | -1.5469 / 0.245 | -1.1875 / 0.305 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC15/B` | FAIL | reject_stage1 | - | -5.2177 | -3.6176 / 0.215 | -2.8448 / 0.267 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC15/C` | FAIL | reject_stage1 | - | -1.0278 | -0.8191 / 0.327 | -0.7506 / 0.336 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC16/A` | FAIL | reject_stage1 | - | -0.3216 | -1.5482 / 0.397 | -0.8505 / 0.430 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC16/B` | FAIL | reject_stage1 | - | -0.5434 | -0.5154 / 0.443 | -0.1627 / 0.480 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC16/C` | FAIL | reject_stage1 | - | -0.5943 | -0.4316 / 0.411 | -0.3885 / 0.429 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC17/A` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IC17/B` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IC17/C` | n/a | not_applicable | - | - | - | - | - | - | - | - | not applicable (main window (stage 1)) |
| `IC18/A` | FAIL | reject_stage1 | - | -1.3381 | -0.7813 / 0.421 | -0.4248 / 0.451 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC18/B` | FAIL | reject_stage1 | - | -0.4808 | -0.1754 / 0.458 | -0.0076 / 0.499 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC18/C` | FAIL | reject_stage1 | - | -1.1611 | +0.0127 / 0.506 | +0.0621 / 0.515 | - | - | - | 0.9835 | rejected (stage 1) |
| `IC19/A` | FAIL | reject_stage1 | - | -0.9414 | -1.3183 / 0.137 | -1.1329 / 0.169 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC19/B` | FAIL | reject_stage1 | - | -1.6935 | -2.6005 / 0.132 | -2.4137 / 0.160 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC19/C` | FAIL | reject_stage1 | - | -0.5696 | -0.4273 / 0.301 | -0.4361 / 0.296 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC20/A` | FAIL | reject_stage1 | - | -0.4890 | -0.0000 / 0.428 | -0.0000 / 0.448 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC20/B` | FAIL | reject_stage1 | - | -0.0546 | -0.0000 / 0.453 | -0.0000 / 0.454 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC20/C` | FAIL | reject_stage1 | - | -0.4890 | -0.0000 / 0.431 | -0.0000 / 0.452 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC21/A` | FAIL | reject_stage1 | - | -1.8724 | -0.5593 / 0.402 | -0.0739 / 0.484 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC21/B` | FAIL | reject_stage1 | - | -0.8097 | -0.7581 / 0.395 | -0.2406 / 0.456 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC21/C` | FAIL | reject_stage1 | - | -0.7414 | -0.2001 / 0.450 | -0.1669 / 0.465 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC22/A` | FAIL | reject_stage1 | - | -0.4714 | -0.2629 / 0.402 | -0.0107 / 0.499 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC22/B` | FAIL | reject_stage1 | - | -1.3352 | -0.8073 / 0.232 | -0.6620 / 0.270 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC22/C` | FAIL | reject_stage1 | - | -0.7898 | -0.0790 / 0.458 | -0.0621 / 0.460 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC23/A` | FAIL | reject_stage1 | - | -4.7518 | -3.4215 / 0.007 | -1.7077 / 0.056 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC23/B` | FAIL | reject_stage1 | - | -2.9542 | -1.8389 / 0.008 | -0.7805 / 0.064 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC23/C` | FAIL | reject_stage1 | - | -3.0760 | -2.3124 / 0.070 | -0.5421 / 0.317 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC24/A` | FAIL | reject_stage1 | - | -0.9987 | -0.6562 / 0.110 | -0.2881 / 0.293 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC24/B` | FAIL | reject_stage1 | - | -0.0680 | -0.0333 / 0.388 | -0.0209 / 0.452 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC24/C` | FAIL | reject_stage1 | - | -0.6380 | -0.3679 / 0.238 | -0.1487 / 0.359 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC25/A` | FAIL | reject_stage1 | - | -0.3900 | +0.4872 / 0.631 | +0.8743 / 0.696 | - | - | - | 0.0000 | rejected (stage 1) |
| `IC25/B` | FAIL | reject_stage1 | - | -0.2473 | -0.1450 / 0.417 | +0.1586 / 0.597 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC25/C` | FAIL | reject_stage1 | - | -3.0446 | -2.1667 / 0.041 | -1.1779 / 0.104 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC26/A` | FAIL | reject_stage1 | - | -0.5780 | -0.3517 / 0.307 | -0.3298 / 0.336 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC26/B` | FAIL | reject_stage1 | - | -1.1631 | -0.8194 / 0.282 | -0.7180 / 0.321 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC26/C` | PASS | reject_stage2 | G1_base, G5_base, G6_calibrated, AT_2010_01_to_end, AT_2017_01_to_end, B1_recent, B2_flat, E1, FRESH | +0.0000 | +0.0000 / 0.510 | +0.0000 / 0.513 | +0.0000 | -0.0000 | FAIL | 1.0000 | rejected (stage 2) |
| `IC27/A` | FAIL | reject_stage1 | - | -1.6064 | -0.1545 / 0.369 | -0.1238 / 0.401 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC27/B` | FAIL | reject_stage1 | - | -2.7535 | -0.2589 / 0.374 | -0.1833 / 0.416 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC27/C` | FAIL | reject_stage1 | - | +0.0057 | -0.0000 / 0.489 | -0.0000 / 0.495 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC28/A` | FAIL | reject_stage1 | - | -2.8078 | -0.4567 / 0.430 | -0.3883 / 0.437 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC28/B` | FAIL | reject_stage1 | - | -2.1866 | -0.3878 / 0.390 | -0.2297 / 0.425 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC28/C` | FAIL | reject_stage1 | - | -0.0750 | -0.2002 / 0.394 | -0.2059 / 0.390 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC29/A` | FAIL | reject_stage1 | - | -1.6230 | -0.6315 / 0.400 | -0.5368 / 0.413 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC29/B` | FAIL | reject_stage1 | - | -0.9561 | -0.4798 / 0.360 | -0.0525 / 0.475 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC29/C` | FAIL | reject_stage1 | - | +0.0956 | -0.2259 / 0.355 | -0.2327 / 0.351 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC30/A` | FAIL | reject_stage1 | - | -0.6488 | -0.0498 / 0.387 | -0.0208 / 0.418 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC30/B` | FAIL | reject_stage1 | - | -0.0581 | -0.0000 / 0.483 | -0.0000 / 0.497 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC30/C` | FAIL | reject_stage1 | - | -0.0856 | -0.0000 / 0.441 | -0.0000 / 0.428 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC47/A` | FAIL | reject_stage1 | - | -0.2875 | -0.2217 / 0.378 | -0.2211 / 0.370 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC47/B` | FAIL | reject_stage1 | - | -0.4184 | -0.3224 / 0.374 | -0.3238 / 0.359 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC47/C` | PASS | reject_stage2 | G7, AT_2017_01_to_end | +0.1195 | +0.0963 / 0.600 | +0.1082 / 0.606 | -0.0343 | +0.0442 | PASS | 0.0000 | rejected (stage 2) |
| `IC48/A` | FAIL | reject_stage1 | - | -4.7302 | -2.0865 / 0.225 | -1.2707 / 0.323 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC48/B` | FAIL | reject_stage1 | - | -5.0861 | -2.8305 / 0.211 | -1.8117 / 0.303 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC48/C` | FAIL | reject_stage1 | - | -0.2551 | -0.6275 / 0.259 | -0.4061 / 0.354 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC50/A` | FAIL | reject_stage1 | - | -0.5732 | -0.5323 / 0.350 | -0.3285 / 0.401 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC50/B` | FAIL | reject_stage1 | - | -0.5014 | -0.5264 / 0.359 | -0.3180 / 0.405 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC50/C` | FAIL | reject_stage1 | - | +0.0979 | -0.0071 / 0.485 | -0.0146 / 0.474 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC51/A` | FAIL | reject_stage1 | - | -1.7377 | -1.1435 / 0.124 | -0.9260 / 0.167 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC51/B` | FAIL | reject_stage1 | - | -0.9848 | -0.5545 / 0.299 | -0.5196 / 0.317 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC51/C` | FAIL | reject_stage1 | - | -2.6084 | -1.5587 / 0.153 | -1.4177 / 0.175 | - | - | - | 1.0000 | rejected (stage 1) |
| `IC60/A` | PASS | reject_stage2 | G2_base, G2_calibrated, G4, AT_2017_01_to_end, E2 | +0.1745 | +0.0355 / 0.573 | +0.0508 / 0.604 | +0.0905 | +0.0941 | PASS | 0.9780 | rejected (stage 2) |
| `IC60/B` | FAIL | reject_stage1 | - | -0.0142 | +0.0013 / 0.512 | +0.0050 / 0.537 | - | - | - | 0.9975 | rejected (stage 1) |
| `IC60/C` | PASS | reject_stage2 | G2_base, G2_calibrated, G4, AT_2017_01_to_end, E2 | +0.1103 | +0.0721 / 0.601 | +0.0797 / 0.616 | +0.1071 | +0.1447 | PASS | 0.3350 | rejected (stage 2) |

