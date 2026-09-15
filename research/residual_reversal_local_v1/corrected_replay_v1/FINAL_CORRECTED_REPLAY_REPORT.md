# Final corrected replay report: equity short-term residual reversal v1

## Decision

`RESEARCH_REJECT`

## Replay validity

`CORRECTED_REPLAY_VALID`

## Starting state

Branch: `research/residual-reversal-local-v1`. Replay-start HEAD: `cb980ff784f8b9512ec486eb9bb145f38fdc74ee`. Main: `8123694a71d8fbb0e520f13b4e1692e932754e97`. Forensic parent: `633c11448387f291d20a8a1aa0ed1286e2e94b34`.

## Frozen economics confirmation

The candidate, 21-session formation, zero skip, 273 aligned-return beta window, 63 residual-volatility window, negative-first ranking, no positive gate, top_n=6, ivol q=0.70, inverse-vol sizing, weekly cadence, 0.10 position cap, V3 control, 75/25 treatment allocation, chronology, costs, risk limits, and frozen acceptance gates were unchanged. No economic parameter was changed after any outcome was observed.

## Corrections applied

### Lifecycle repair

The v5 inactive-entity terminal map was loaded into a research-only engine subclass. Held inactive symbols close on their real final bar with the existing accounting path, pending orders are removed, and the symbol is excluded from later completeness requirements without fake prices.

### Decision coverage repair

Every scheduled attempt is persisted separately from fills. Coverage thresholds were frozen before outcomes: at least 90% eligible weekly decision coverage, no more than 8 consecutive skipped eligible weeks, validation decisions required, and no more than 8 eligible weeks of final lag.

### Strategy-specific observation sets

V3 was ranked by V3 alpha, reversal by the frozen reversal alpha, and treatment by the predeclared balanced V3/reversal round-robin union. The scanner eligible pool remained shared.

### Sleeve-aware position-cap handling

Treatment uses a one-account ledger with source-preserving contributions, 4/2 six-slot sleeve quotas, overlap-aware one-slot selection, proportional 0.10 per-symbol capping, explicit dropped-symbol reasons, and cash-held infeasible remainder. No capital is transferred between sleeves.

### Verifier strengthening

The separate verifier checks identity, candidate semantics, observation identity, lifecycle fixtures, coverage, sleeve attribution, risk caps, fills, costs, NAV, and cash before the frozen economic gates are interpreted.

## Data identity

| Item | SHA / value |
| --- | --- |
| Price panel | bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139 |
| Corporate actions | a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23 |
| Entity lifetimes | ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596 |
| Frozen protocol | bf8a47c20d35835920fb15ac2e75eaf0ea5ebaec3a4f79a0c62e9aa515bf442a |
| Corrected replay spec | 5981801f9b44c70eb77d0cba55ed7336bdf7d3840ce639651898753fe9d197f7 |

## Decision coverage

| Strategy | Period | Scheduled | Decisions | Coverage | Fills | Last decision | Longest gap |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V3 | development | 256 | 256 | 100.00% | 1015 | 2021-12-27T05:00:00+00:00 | 0 |
| V3 | validation | 239 | 239 | 100.00% | 1039 | 2026-07-27T04:00:00+00:00 | 0 |
| Reversal | development | 256 | 256 | 100.00% | 1767 | 2021-12-27T05:00:00+00:00 | 0 |
| Reversal | validation | 239 | 239 | 100.00% | 1581 | 2026-07-27T04:00:00+00:00 | 0 |
| Treatment | development | 256 | 256 | 100.00% | 1418 | 2021-12-27T05:00:00+00:00 | 0 |
| Treatment | validation | 239 | 239 | 100.00% | 1411 | 2026-07-27T04:00:00+00:00 | 0 |

Validation decisions, fills, first/last decision, skipped-attempt reasons, and longest gaps are independently persisted in `decision_coverage.json` and verified before this decision.

## Observation-set identity

V3: `['equity_residual_momentum_v3']`; reversal: `['equity_short_term_residual_reversal_v1']`; treatment: `['treatment_combined_observation_v1']`. Treatment snapshots with missing reversal top-six access: `0`.

## Standalone reversal results

| Cost | Net return | CAGR | Volatility | Sharpe | Sortino | Max DD | Calmar | Turnover | Fills |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.8849 | 0.0684 | 0.1118 | 0.6483 | 0.8499 | 0.1778 | 0.3849 | 302.5174 | 3348 |
| 2 | 0.7235 | 0.0585 | 0.1117 | 0.5647 | 0.7412 | 0.1791 | 0.3266 | 302.5235 | 3348 |
| 3 | 0.5747 | 0.0486 | 0.1117 | 0.4803 | 0.6310 | 0.1804 | 0.2691 | 302.5133 | 3349 |

## V3

At 1x: net return `0.9821`, CAGR `0.0741`, volatility `0.1126`, Sharpe `0.6910`, Sortino `0.8877`, max drawdown `0.2044`, Calmar `0.3624`, turnover `168.4613`. Full 1x/2x/3x control outputs are in `v3_control.json`.

## Diversification

Full-period daily return correlation: `0.4761` over `2407` observations; monthly correlation: `0.5328`. Holdings overlap and drawdown overlap are in `diversification.json`.

## Corrected treatment

Intended sleeves were V3 `0.41250000000000003` and reversal `0.1375`. At 1x, mean realized target exposure was V3 `0.2066` and reversal `0.0501`; mean unused allocation was V3 `0.2059` and reversal `0.0874`.

## Sleeve attribution table

| Metric | V3 | Reversal |
| --- | --- | --- |
| Intended allocation | 0.41250000000000003 | 0.1375 |
| Mean realized target exposure | 0.2066 | 0.0501 |
| Mean unused allocation | 0.2059 | 0.0874 |
| Zero-exposure decisions | 0 | 0 |

## Capacity

The six-position policy produced allocation-capacity shortfall on `495` of `495` 1x treatment decisions (`100.00%`). Overlap decisions: `251`. Reversal zero-exposure reasons: `{}`. Infeasible remainder stayed cash.

## Development vs validation

| Run | Window | Net return | CAGR | Sharpe | Sortino | Max DD | Turnover |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V3 | development | 0.6098 | 0.1000 | 0.8770 | 1.0677 | 0.1553 | 82.3755 |
| V3 | validation | 0.2313 | 0.0465 | 0.4740 | 0.6502 | 0.1896 | 84.8297 |
| Reversal 1x | development | 0.5855 | 0.0967 | 0.9350 | 1.1581 | 0.1778 | 161.0558 |
| Reversal 1x | validation | 0.1888 | 0.0386 | 0.3768 | 0.5230 | 0.1216 | 142.2252 |
| Treatment 1x | development | 0.2155 | 0.0398 | 0.6913 | 0.8323 | 0.0860 | 55.0798 |
| Treatment 1x | validation | 0.1441 | 0.0299 | 0.5684 | 0.7765 | 0.0617 | 55.1782 |

## Cost stress

The unchanged 1x/2x/3x equity cost model is persisted in `cost_stress.json`; no cost was altered after observing results.

## Concentration

Treatment robustness and leave-one-symbol-out diagnostics are in `robustness.json`. The 1x maximum realized concentration was `0.0934`.

## Independent verification

Verifier status: `CORRECTED_REPLAY_VALID`. Checks passed: `12429`; failures: `0`. Lifecycle terminal-bar events, coverage, observation identities, source attribution, cap/cash invariants, fills, costs, NAV, and cash were checked before economic interpretation.

## Frozen gate table

| Gate | Status | Evidence |
| --- | --- | --- |
| standalone viability | PASS | {"full_cagr": 0.06843688103205103, "validation_return": 0.188826269386277} |
| cost robustness | PASS | {"2x_net_return": 0.7234679922914791, "3x_net_return": 0.5746776334130668} |
| diversification | PASS | {"daily_correlation": 0.47613295408360073} |
| capital-neutral portfolio contribution | FAIL | {"improvements": {"cagr": false, "calmar": true, "sharpe": false, "sortino": false}, "non_improving_metrics_within_2pp": false} |
| drawdown | PASS | {"control_max_drawdown": 0.20435850553058274, "treatment_max_drawdown": 0.08600528946039925} |
| validation contribution | PASS | {"control_validation": {"cagr": 0.046546521357007764, "max_drawdown": 0.18959250492510563, "sharpe": 0.4740490862395633}, "treatment_validation": {"cagr": 0.029880104612926228, "max_drawdown": 0.06170547176070254, "sharpe": 0.56837345761963}} |
| turnover economics | PASS | {"control_cost_drag": 0.10932306551741089, "control_turnover": 168.46128789996362, "treatment_cost_drag": 0.04948230395299169, "treatment_turnover": 110.56373206621083} |
| concentration | PASS | {"interpretation": "qualitative gate operationalized by breadth, contribution share, and leave-one diagnostic", "largest_absolute_symbol_share": 0.02159630031358127, "leave_one_approximate_return": 0.35888641676885835, "maximum_realized_weight_concentration": 0.09336249858926353, "unique_filled_symbols": 354} |

## Comparison structural-only

This is structural only. The original result JSON and report were not used as economic evidence. The corrected run removes the original zero-validation-decision interpretation, wires lifecycle handling, gives reversal its own observation ranker, and preserves treatment source attribution. Any corrected economic decision below is based only on the valid corrected replay and the frozen gates.

## Final reasoning

Replay validity is `CORRECTED_REPLAY_VALID` and the final economic decision is `RESEARCH_REJECT`. The result is research-only; it does not enable PAPER/LIVE, register the reversal strategy in production, or place broker orders.

## Files

- `CORRECTED_REPLAY_SPEC.md`
- `replay_manifest.json`, `corrected_replay.py`, `corrected_mechanics.py`, `verify_corrected_replay.py`, `finalize_corrected_replay.py`
- `test_corrected_mechanics.py`, `test_corrected_replay.py`
- `observation_sets.json`, `lifecycle_audit.json`, `decision_coverage.json`, `sleeve_attribution.json`
- `v3_control.json`, `standalone_reversal.json`, `treatment.json`, `cost_stress.json`, `diversification.json`, `robustness.json`, `execution_audit.json`, `corrected_independent_verification.json`, `decision.json`, `gate_table.json`
- `FINAL_CORRECTED_REPLAY_REPORT.md`

## Final branch SHA

`a96efd1f8e2c1f900091e90f072301a34665719a` (serialized result/artifact commit)
