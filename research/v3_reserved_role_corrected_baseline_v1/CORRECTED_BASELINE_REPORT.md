# Corrected V3 Baseline Report

Final decision: **`CORRECTED_V3_BASELINE_FAILED`**

The role-isolation maintenance fix is valid and the corrected replay is internally verified. The corrected V3 baseline passes return, risk-adjusted, cost-stress, lifecycle, execution, coverage, and role-control gates except the frozen 1x full-period maximum-drawdown gate: `20.4359%` versus the predeclared `5.0%` production threshold. The result is therefore failed on the frozen protocol; no role is restored for performance and no PAPER/LIVE promotion is made.

## Part I — maintenance and structural proof

The pre-fix production SHA was `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`. The explicit role defect was recorded in [RESERVED_ROLE_DEFECT_RECEIPT.md](../../RESERVED_ROLE_DEFECT_RECEIPT.md): the deterministic pre-fix probe ranked and targeted `SH`, `PSQ`, and `DOG`, while preserving `SPY` as an observation/reference pin.

The narrow fix was implemented as maintenance commit `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53` and fast-forwarded to `main` and `origin/main`. It is configuration-derived rather than a broad ETF/ETN taxonomy change:

- `SPY` remains reference-only.
- `SH`, `PSQ`, and `DOG` remain downloadable, streamable, pinned, managed, and available to explicitly authorized hedge paths.
- All four are excluded only from ordinary V3 alpha targeting.
- The current broad `US_EQUITY` candidate universe, V3 parameters, allocation, risk caps, execution policy, PAPER mode, and LIVE-disabled state are unchanged.

The focused role tests passed, including raw-score/order preservation for remaining ordinary names, observation-pin preservation, managed holding safety, configuration-derived roles, and authorized hedge opt-in. Phase A verification passed:

```
1074 passed, 1 skipped, 2 warnings
ruff check .                         PASS
ruff format --check .                PASS
mypy src                             PASS (131 source files)
```

The six localhost socket tests initially blocked by the sandbox were rerun with the required host permission and passed; the complete suite then passed.

## Part II — corrected replay

The replay branch is `research/v3-reserved-role-corrected-baseline-v1`, based on corrected production SHA `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`. The frozen protocol is [CORRECTED_BASELINE_PROTOCOL.md](CORRECTED_BASELINE_PROTOCOL.md); the durable structural comparison was written before economic outputs in [structural_old_vs_corrected.json](structural_old_vs_corrected.json).

### Data and windows

- Bars: `equity_pit_symmetric_2016_2026_v5.csv`, SHA-256 `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`.
- Entity lifetimes: `equity_entity_lifetimes_full_v5.csv`, SHA-256 `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`.
- Corporate actions: `equity_survivorship_full_corporate_actions_v5.csv`, SHA-256 `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`.
- Dataset identity: 11,686,832 rows, 9,010 entities, 6,858 active and 2,152 inactive histories, SIP, split-adjusted prices.
- Lifecycle evidence: 2,151 `POSSIBLE_DELISTING` terminal maps; one `EOSEW` `FINAL_SURVIVOR` row is horizon-censored and receives no synthetic terminal event. All 9,010 lifetime first/last bars matched the loaded data.
- Corporate actions: 141,908 cash dividends were applied; split/stock-dividend rows were not re-applied to already split-adjusted bars.
- Evaluation: 2016 warmup; development 2017-01-03 through 2021-12-31; validation 2022-01-03 through 2026-07-30.
- Common endpoint: 2026-07-30, timestamp `2026-07-30T04:00:00+00:00`. Although the broad manifest contains some 2026-07-31 rows, `SPY`, `SH`, `PSQ`, and `DOG` all end on 2026-07-30, so 2026-07-30 is the common reference/hedge endpoint.

The source CSV has two provider spellings (`1d`/`1Day` and lowercase/uppercase market). The research harness normalized only those exact semantic field spellings; it did not rebuild, download, alter, or reprice the canonical data.

### Structural old-vs-corrected result

The same prepared bars, PIT universe, lifecycle map, corporate actions, and engine path were used; only the ordinary-alpha role boundary differed.

- Decisions: `495` old and `495` corrected.
- Evaluation curve timestamps: `2,406` expected and `2,406` corrected.
- PIT snapshots: `2,657`.
- `SPY`, `SH`, `PSQ`, and `DOG` were present in candidate-pool and observation context on all `495` decisions.
- Old target role hits: `0` on this broad historical replay.
- Corrected target role hits: `0` for each of `SPY`, `SH`, `PSQ`, and `DOG`.
- Old-vs-corrected target changes: `0`; affected roles and replacements: none.

This zero-delta full replay does not erase the pre-fix defect: the receipt and deterministic focused test exercise the explicit failure fixture. It shows that the broad v5 dataset did not happen to select those reserved hedge roles into an old V3 target, while the corrected boundary now makes that invariant structural and testable.

### Economic metrics

Returns, volatility, Sharpe, Sortino, drawdown, and Calmar are percentages/ratios. Turnover is one-way traded notional divided by average equity. Costs are dollars and include slippage, spread, regulatory fees, and terminal liquidation costs. “Fills” are decision-driven fills; terminal liquidations are shown separately.

| Cost | Period | Net return | CAGR | Vol | Sharpe | Sortino | Max DD | Calmar |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1x | Development | 60.9781% | 10.0015% | 11.6464% | 0.8770 | 1.0681 | 15.5323% | 0.6439 |
| 1x | Validation | 22.7203% | 4.5736% | 10.8292% | 0.4672 | 0.6410 | 18.9593% | 0.2412 |
| 1x | Full | 97.5529% | 7.3714% | 11.2618% | 0.6880 | 0.8841 | 20.4359% | 0.3607 |
| 2x | Development | 57.0085% | 9.4530% | 11.6452% | 0.8341 | 1.0163 | 15.5685% | 0.6072 |
| 2x | Validation | 19.6335% | 3.9934% | 10.8331% | 0.4157 | 0.5702 | 19.5058% | 0.2047 |
| 2x | Full | 87.8348% | 6.8071% | 11.2629% | 0.6412 | 0.8240 | 21.3095% | 0.3194 |
| 3x | Development | 53.1652% | 8.9112% | 11.6461% | 0.7914 | 0.9649 | 15.6047% | 0.5711 |
| 3x | Validation | 16.6230% | 3.4160% | 10.8310% | 0.3643 | 0.5000 | 20.0019% | 0.1708 |
| 3x | Full | 78.6259% | 6.2476% | 11.2625% | 0.5945 | 0.7644 | 22.1282% | 0.2823 |

| Cost | Period | Turnover | Costs | Fills | Terminal liqs | Avg exposure | Avg positions | Max concentration |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1x | Development | 82.3617 | $3,243.15 | 1,015 | 2 | 50.5815% | 5.5012 | 10.8215% |
| 1x | Validation | 84.8457 | $4,286.42 | 1,039 | 3 | 52.8424% | 5.7535 | 12.3386% |
| 1x | Full | 168.4876 | $7,529.57 | 2,054 | 5 | 51.6579% | 5.6214 | 12.3386% |
| 2x | Development | 82.3951 | $6,301.40 | 1,016 | 2 | 50.5907% | 5.5012 | 10.8228% |
| 2x | Validation | 84.8467 | $8,129.56 | 1,039 | 3 | 52.8580% | 5.7535 | 12.3408% |
| 2x | Full | 168.3931 | $14,430.96 | 2,055 | 5 | 51.6702% | 5.6214 | 12.3408% |
| 3x | Development | 82.4288 | $9,280.15 | 1,016 | 2 | 50.6037% | 5.5012 | 10.8241% |
| 3x | Validation | 84.8359 | $11,680.42 | 1,036 | 3 | 52.8527% | 5.7535 | 12.3429% |
| 3x | Full | 168.2877 | $20,960.57 | 2,052 | 5 | 51.6744% | 5.6214 | 12.3429% |

The small actual concentration overshoot is mark-to-market drift; corrected targets still satisfy the 10% target cap, six-position limit, 60% gross limit, and 40% cash floor.

### Frozen gates and independent verification

The independent verifier is [verify_corrected_baseline.py](verify_corrected_baseline.py), with its persisted result in [independent_verification.json](independent_verification.json). It recomputed period metrics from the persisted NAV curve, holdings history, fills, terminal events, and targets, then rechecked code SHA, config identity, all three dataset hashes, lifecycle counts, PIT coverage, next-open execution, NAV/cash, costs, and role controls.

| Gate | Result | Evidence |
|---|---|---|
| G1 validity/parity | PASS | Hashes, lifecycle, PIT coverage, execution, NAV/cash, target, cost, and compact-metric checks all pass. |
| G2 full 1x return/CAGR positive | PASS | 97.5529% return; 7.3714% CAGR. |
| G3 validation 1x return/CAGR positive | PASS | 22.7203% return; 4.5736% CAGR. |
| G4 full 1x Sharpe/Sortino/Calmar positive | PASS | 0.6880 / 0.8841 / 0.3607. |
| G5 full 1x max drawdown ≤ 5% | **FAIL** | 20.4359% versus the frozen 5.0% production threshold. |
| G6 cost robustness | PASS | 2x full return 87.8348%; 3x full return 78.6259%, max DD 22.1282%. |
| G7 controls and zero reserved targets | PASS | All cost runs: exact zero target count for every reserved role; target controls pass. |

## Remaining universe limitation

This task intentionally did not classify every ETF, ETN, ETP, leveraged product, or ordinary equity. The broad current `US_EQUITY` universe remains in force; only the explicitly configured reference and approved inverse-hedge roles received the narrow ordinary-alpha boundary. The v5 dataset provides strong PIT bars, entity lifetimes, and corporate-action provenance, but it is not a complete historical exchange security-master taxonomy. That limitation is not a reason to broaden the fix or to promote the failed baseline.

## Research-branch inventory and cleanup

Remote evidence was preserved. No local research branch was deleted in this task because the branches remain useful audit evidence and no deletion was needed to establish the corrected production state.

| Branch | Final recorded SHA | Status/verdict |
|---|---|---|
| `research/overnight-intraday-design-v1` | `7ec53efecc4d37cab17d0d96f194389b4513a69b` | `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`; design only, economic test not run. |
| `research/overnight-reversal-v1` | `41e1c9026bb34bbe81f98577844c9eca0523da8a` | `RESEARCH_RUN_VALID` / `RESEARCH_REJECT`. |
| `research/spy-defensive-overlay-v1` | `da9a99b6e23df880fb64afa78d1f38aa70edd5ec` | `RESEARCH_REJECT`. |
| `research/v3-volatility-managed-v1` | `9dbe4bd78b3276159dd089e4edf4e15b9ed1cb01` | `RESEARCH_REJECT`. |
| `research/equity-security-master-source-v1` | `70738106760eec86908b846823b25bd6964b5c0c` | `SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED`. |
| `maintenance/equity-universe-integrity-v1` | `180fd12e7bafb5943856229e85e305d5d9906511` | `UNIVERSE_CONTRACT_AMBIGUOUS_OPERATOR_DECISION_REQUIRED`; no production fix. |

## Final production state

`main`, `origin/main`, and the maintenance branch resolve to `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`. The corrected role boundary is integrated and pushed to production `main`; the research baseline branch is not merged. PAPER remains enabled, LIVE remains disabled, no live authorization was generated, and no production data/configuration/strategy restoration was made after the failed frozen baseline.

The full compact artifacts are in this directory: [protocol](CORRECTED_BASELINE_PROTOCOL.md), [structural comparison](structural_old_vs_corrected.json), [economic evidence](economic_results.json), and [independent verification](independent_verification.json).
