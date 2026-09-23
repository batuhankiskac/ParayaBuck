# Final account-capacity migration V3 report

## Final migration decision

`ACCOUNT_ARCHITECTURE_MIGRATION_8_ACCEPTED`. Structural gate: **PASS**. Next action: `PROCEED_TO_PRODUCTION_PARITY_CHANGE_REVIEW`.

## Starting main SHA and physical-clock V2 closure

Starting production main was `b135d73743ec2652f79b8c840df50bd3f67166e8`. The documentation-only closure was committed on main as `d1c8847aebd3776730ef9ad93967908c44a8aed9` locally and remains unpublished. V2 was classified `PHYSICAL_AVAILABILITY_CLOCK_V2_VALIDATED_WITH_LIMITATIONS` and `MIGRATION_REPLAY_READY`. Its completed branch was removed after preservation by the immutable tag. The obsolete label-clock replay lacks its full event trace, so exact old-clock cross-market claimant flips cannot be recovered.

## Authorities and freeze

The annotated infrastructure tag `infrastructure/physical-availability-clock-v2-cap6-2026-09` peels to `11890e93ad1a5223cb11483488deb4d5a6aec3d3`. The corrected causal crypto baseline is `CRYPTO_CANONICAL_BASELINE_CAUSAL_REPAIR_ACCEPTED` with 454 decisions and 406 fills. Authority receipts pass. The pre-outcome freeze commit is `51b8fa16c77df60729414da17d20d329480e380c` and the verifier confirmed it predates both outcomes. Imported source files initially matched the tag exactly. Only the constructor guard was extended to allow the frozen cap-8 arm; the same replay and input paths ran both arms.

## Arm definitions, period, and input identity

Control: account `maximum_positions=6`. Treatment: account `maximum_positions=8`. The account starts flat with $100,000 cash, no pending orders, and no broker state. First physical event: `2017-01-03T14:30:00+00:00`; final crypto terminal: `2026-07-31T00:00:00+00:00`. Equity V3 and corrected crypto low-variance hysteresis are the only strategies. The frozen config SHA256 is `414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`. Direct source hashes and derived PIT identities are in the freeze JSON; both arm traces match them exactly. Physical timing, same-timestamp ordering, final liquidation, costs, gross, sleeve, and per-name rules are unchanged.

## Structural capacity results

| Metric | Cap 6 | Cap 8 | Difference (8−6) |
|---|---:|---:|---:|
| Count-denied target openings | 1,107 | 433 | -674 |
| Count-denied order admissions | 5 | 5 | 0 |
| Timestamps at global cap | 38,883 | 37,105 | -1,778 |
| Count-specific target notional shortfall sum | 20,740,097.764236 | 12,866,679.676034 | -7,873,418.088202 |
| Average equity positions | 4.279987 | 6.055003 | 1.775017 |
| Average crypto positions | 1.032472 | 1.033135 | 0.000663 |
| Average total positions | 5.312459 | 7.088138 | 1.775679 |
| Peak effective slot claims | 6 | 8 | 2 |

The notional shortfall is summed across distinct decisions; it is not realized idle capital or hypothetical P&L. No unused cash is attributed to the count cap without an observed denial.

## Slot denial attribution

| Cause | Cap 6 | Cap 8 |
|---|---:|---:|
| CRYPTO_POSITION_COUNT | 13 | 23 |
| DAILY_LOSS_LIMIT | 352 | 263 |
| GLOBAL_POSITION_COUNT | 1112 | 438 |

Daily-loss halts take precedence when risk reasons overlap with position count. Pending conflicts are counted separately. Liquidity, price, signal eligibility, cash, and gross/sleeve/per-name causes are not inferred when the trace lacks a deterministic rejection reason.

## Cross-market crowd-out

See `CROSS_MARKET_CROWDOUT_V3.md` for both directions, affected symbols/timestamps, slot state, and target shortfall. Event-level rejections have no defined lasting duration.

## Structural migration gate

Cap 6 observed 1107 count-denied target openings and 5 count-denied order admissions. Cap 8 observed 433 and 5. The count-specific total decreased from 1112 to 438. This is an architecture result, independent of return ranking.

## Exposure and risk invariants

| Metric | Cap 6 | Cap 8 | Difference (8−6) |
|---|---:|---:|---:|
| Maximum target gross | 0.550000 | 0.600000 | 0.050000 |
| Maximum observed gross | 0.589767 | 0.615507 | 0.025740 |
| Average realized gross | 0.436482 | 0.506752 | 0.070270 |
| Average cash | 75,799.866447 | 69,292.080399 | -6,507.786048 |

Both arms pass cash, inventory, slot, gross target, sleeve, per-name, reservation, corporate-action, causality, and deterministic replay gates. Realized gross can drift between decisions; the unchanged 0.60 target limit remains enforced.

Cap 6 had 0 of 50194 account marks above 0.60 realized gross; cap 8 had 9152 of 50194. Treatment peaked at 0.615507 on `2024-01-11T00:00:00+00:00`, when the recorded target gross was 0.589499. Cash/NAV/gross arithmetic reconciles. This is fuller target use followed by mark-to-market drift, not a change to the nominal target constraint. The production parity review must examine this excursion before deployment.

## Concentration comparison

| Metric | Cap 6 | Cap 8 | Difference (8−6) |
|---|---:|---:|---:|
| Average total-account HHI (daily last marks) | 0.040183 | 0.039860 | -0.000323 |
| Maximum trace-valued name weight | 0.117989 | 0.119854 | 0.001865 |
| Maximum target name weight | 0.100000 | 0.100000 | 0.000000 |

| Metric | Cap 6 | Cap 8 | Difference (8−6) |
|---|---:|---:|---:|
| Equity HHI, active days | 0.264024 | 0.179849 | -0.084176 |
| Crypto HHI, active days | 0.626870 | 0.664209 | 0.037339 |

Per-name target caps are unchanged (equity 0.10, crypto 0.05). The event trace omits marks for symbols before they become holdings, so intraday fill-time HHI is a trace-price proxy; daily last-mark sleeve HHI is reported separately. Lower HHI is not treated as alpha evidence.

## Operational load

| Metric | Cap 6 | Cap 8 | Difference (8−6) |
|---|---:|---:|---:|
| Peak actual positions | 6 | 8 | 2 |
| Peak occupied/reserved claims | 6 | 8 | 2 |
| Peak working orders | 8 | 11 | 3 |
| Orders per decision | 2.173867 | 2.665964 | 0.492097 |
| Orders | 2,063 | 2,530 | 467 |
| Fills | 2,297 | 2,835 | 538 |
| Account reconciliation marks | 50,194 | 50,194 | 0 |
| Reservations created | 2,063 | 2,530 | 467 |
| Reservations released | 2,063 | 2,530 | 467 |
| Pending-order conflicts | 8,243 | 9,571 | 1,328 |

The treatment remains within eight occupied/reserved claims. Focused pagination and shared-slot tests passed; no broker API call was made.

## Economic comparison

| Metric | Cap 6 | Cap 8 | Difference (8−6) |
|---|---:|---:|---:|
| Total Return | 0.586520 | 0.858055 | 0.271535 |
| Cagr | 0.047462 | 0.064221 | 0.016759 |
| Annualized Volatility | 0.110563 | 0.111862 | 0.001299 |
| Sharpe | 0.560468 | 0.726118 | 0.165650 |
| Sortino | 0.764979 | 1.003143 | 0.238165 |
| Max Drawdown | -0.218973 | -0.199396 | 0.019578 |
| Calmar | 0.216748 | 0.322077 | 0.105329 |
| Ending Nav | 158,651.991627 | 185,805.518813 | 27,153.527186 |
| Turnover | 145.077166 | 148.883346 | 3.806180 |
| Total Modeled Costs | 8,543.516067 | 9,434.707619 | 891.191552 |
| Average Cash | 75,799.866447 | 69,292.080399 | -6,507.786048 |
| Average Realized Gross | 0.436482 | 0.506752 | 0.070270 |

One frozen full-period look was used. No performance threshold, cap search, strategy retuning, or new partition was introduced. Costs and risk regime must be read with the structural changes; economics do not rescue a failed structural or correctness gate.

## Determinism and independent verification

Both arms were run twice. Decision, order, fill, reservation, position, cash, NAV, complete event, and final-state hashes matched within each arm. The independent verifier read compressed raw traces and recomputed event/component hashes, ledger cash, inventory count, slot claims, target limits, order and fill counts, costs, turnover, direct count denials, and economic metrics. Its status is `PASS`. Full NAV valuation at fill-time cannot be reconstructed independently from a trace that omits non-held source marks; validated replay internal NAV reconciliation remains a separate gate.

## Decision rationale and governance

The result follows the predeclared structural count gate, not historical performance chasing. Previously `ALPHA_VALID_PORTFOLIO_REJECTED` families may receive a separate rerun-eligibility review if migration is accepted; none were rerun here. The old crypto giveback audit was not used and needs a separate causal-baseline refresh.

Marginal Crypto→Equity and Equity→Crypto crowd-out events did not disappear under cap 8; both directions increased slightly as the larger account held more incumbent positions. Aggregate count-specific denials still fell from 1112 to 438. Cap 8 does not reserve two slots for crypto, and no separate sleeve slot policy was introduced.

## Production state and publication

Production `maximum_positions` remains 6. Equity V3, crypto strategy and rebalance band, PAPER/LIVE modes, and production code/config remain unchanged. A production parity review is the next action if accepted. The clock-closure commit is `d1c8847aebd3776730ef9ad93967908c44a8aed9`; local `refs/heads/main` is currently `af7225e1952c20a81f7d22d4f8d98126f02e27b3` and also contains an independently created later commit. No main push is part of this study. Local `refs/heads/research/account-capacity-migration-8-v3` requires exact publication approval before push; no new migration tag was created. The final branch SHA is supplied with the completed local commit in the delivery message.
