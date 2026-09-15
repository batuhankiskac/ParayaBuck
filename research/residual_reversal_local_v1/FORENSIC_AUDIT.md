# Forensic audit: equity short-term residual reversal v1

## Forensic verdict

`EXPERIMENT_INVALID_IMPLEMENTATION`

The original `RESEARCH_REJECT` is preserved as an immutable historical artifact, but it is **not admissible as candidate-rejection evidence** because the frozen experiment was not implemented/replayed with valid decision coverage, lifecycle handling, sleeve attribution, or strategy-specific observation sets.

## Branch / artifact identity

- Audit starting HEAD recorded before inspection: `50938a3ac8b899a7528158bc0019808646a196e5`.
- Branch: `research/residual-reversal-local-v1`; origin matched the starting HEAD at audit start: `50938a3ac8b899a7528158bc0019808646a196e5`.
- `main`: `8123694a71d8fbb0e520f13b4e1692e932754e97`.
- Existing campaign artifact commit: `4b6dd3c`.
- Forensic artifact commit: `7e63668`.
- Economic rerun performed: **no**.

Original campaign files were not overwritten. The audit reads the persisted run artifacts, the persisted universe snapshot, and the v5 lifetime table. It does not call `run_campaign.py`, `PortfolioBacktestEngine`, or any return-generating path.

## Finding 1 — validation decision coverage

The three 1x runs contain no recorded normal scheduled decision at or after `2022-01-03T00:00:00+00:00`. Their nonzero validation returns are therefore passive carry/mark-to-market of positions established in development, plus dividends and the engine's end-of-run liquidation adjustment; they are not active out-of-sample strategy validation.

| Strategy | First rebalance | Last rebalance | First fill | Last decision-driven fill | Rebalances | Fills |
| --- | --- | --- | --- | --- | ---: | ---: |
| V3 | 2017-02-06T05:00:00+00:00 | 2018-12-17T05:00:00+00:00 | 2017-02-07T05:00:00+00:00 | 2018-12-18T05:00:00+00:00 | 97 | 392 |
| Reversal | 2017-02-06T05:00:00+00:00 | 2021-12-13T05:00:00+00:00 | 2017-02-07T05:00:00+00:00 | 2021-12-14T05:00:00+00:00 | 252 | 1360 |
| Treatment | 2017-02-06T05:00:00+00:00 | 2018-12-17T05:00:00+00:00 | 2017-02-07T05:00:00+00:00 | 2018-12-18T05:00:00+00:00 | 97 | 459 |

The original period calculator selected curve points in the validation date range and computed returns from those points; it did not require a validation decision or fill. Full-run dividend totals are retained in the original result artifacts, but no validation-period dividend ledger was persisted. Terminal events for inactive holdings are timestamped at their last available bar even though the actual liquidation is performed only after the full curve has been processed.

## Finding 2 — terminal lifecycle / completeness gate

Confirmed defect: the runner hashes `equity_entity_lifetimes_full_v5.csv` but does not pass a terminal/lifecycle map into `PortfolioBacktestEngine`. The engine reads an optional `terminal_map` with a default empty mapping; there is no `self.terminal_map = ...` assignment in the engine or runner.

The engine then reserves managed positions in `_effective_observation_symbols`. With the map absent, an inactive managed symbol remains active for the completeness test. At the next weekly opportunity, its missing current bar causes `_decide` to return before strategy decision and target application.

| Strategy | First permanent skipped opportunity | Symbol first missing opportunity | Symbol | Last available bar | Entity status | Terminal class |
| --- | --- | --- | --- | --- | --- | --- |
| v3 | 2018-12-24T05:00:00+00:00 | 2018-12-31T05:00:00+00:00 | DVMT | 2018-12-27T05:00:00+00:00 | inactive | POSSIBLE_DELISTING |
| v3 | 2018-12-24T05:00:00+00:00 | 2018-12-24T05:00:00+00:00 | ESRX | 2018-12-20T05:00:00+00:00 | inactive | POSSIBLE_DELISTING |
| reversal | 2021-12-20T05:00:00+00:00 | 2021-12-20T05:00:00+00:00 | KSU | 2021-12-13T05:00:00+00:00 | inactive | POSSIBLE_DELISTING |
| treatment | 2018-12-24T05:00:00+00:00 | 2018-12-31T05:00:00+00:00 | DVMT | 2018-12-27T05:00:00+00:00 | inactive | POSSIBLE_DELISTING |
| treatment | 2018-12-24T05:00:00+00:00 | 2018-12-24T05:00:00+00:00 | ESRX | 2018-12-20T05:00:00+00:00 | inactive | POSSIBLE_DELISTING |

The wedge is visible in existing evidence:

- V3 and treatment last decide on `2018-12-17`; the first permanently skipped weekly opportunity is `2018-12-24`, with `ESRX` held and last observed on `2018-12-20`. `DVMT` then reaches its last bar on `2018-12-27`.
- Reversal last decides on `2021-12-13`; the first permanently skipped weekly opportunity is `2021-12-20`, with `KSU` held and last observed on `2021-12-13`.
- The earlier isolated skipped week on `2018-10-01` is not the permanent wedge; later decisions resumed. The permanent tails do not.

The `FINAL_BAR` events in `execution_audit.json` are not evidence that lifecycle handling worked. They are the separate `liquidate_at_end` cleanup after the run; for stale symbols they use the old last bar and mutate the final curve value.

## Finding 3 — six-position treatment truncation

Historical raw sleeve attribution is unavailable. `_rebalance_dict` persists only the merged post-overlay target and execution fields; it does not persist raw V3 names, raw reversal names, overlap, source ranks, or pre-cap merged targets. Therefore no historical V3/reversal exposure split is invented here.

Observed treatment evidence: all 97 recorded treatment decisions carry `position_count_limit`; post-cap target counts are `{"4": 4, "5": 3, "6": 90}`. Post-cap invested exposure ranged from `0.203613` to `0.423958`, with mean `0.347410`. This is under the intended 0.55 sleeve before considering the separate risk-halt effects.

| Decision | Post-cap names | Post-cap invested | Overlay reasons |
| --- | --- | ---: | --- |
| 2017-02-06T05:00:00+00:00 | FFIV, GD, JPM, LNC, SCHW, STT | 0.378125 | position_count_limit |
| 2018-04-02T04:00:00+00:00 | ADBE, CME, EL, PVH, RHT | 0.214361 | daily_loss_limit, position_count_limit |
| 2018-12-17T05:00:00+00:00 | BSX, DVMT, ESRX, FOX | 0.203613 | daily_loss_limit, position_count_limit |

Pure synthetic target probe, with no market data or returns:

- Pre-cap: 8 V3 names, 6 reversal names, zero overlap, 14 merged names.
- Every merged target has `rank=0`.
- The production overlay keeps six V3 names by larger target weight, drops all reversal names, realizes V3 exposure `0.309375`, reversal exposure `0`, and leaves `0.240625` of the intended 0.55 sleeve unused.
- Overlay reason: `position_count_limit`.

Thus the implementation did not prove realization of `0.4125 V3 + 0.1375 reversal`; it made that split vulnerable to systematic position-cap truncation. A historical sleeve-share distribution requires corrected per-decision attribution metadata.

## Finding 4 — observation-set contamination

| Layer | Actual methodology | Own alpha ranks broad eligible pool? | Finding |
| --- | --- | --- | --- |
| Scanner eligible pool | Daily PIT scanner on trailing 60-day metrics, Stage-A shortlist capped at 300 by median dollar volume | n/a — this is the broad pool | Conforms |
| V3 observation set | `build_observation_set` ranked the scanner pool with V3 alpha, plus pinned references, up to 30 symbols | Yes | Conforms |
| Standalone reversal | Reused the same `universe_obj` and V3-ranked `snapshot.observation_set`; reversal scored only visible history | No | `HIDDEN_V3_ALPHA_PREFILTER_CONFIRMED` |
| Treatment | Reused the same V3 observation set for both component decisions | No | Shared V3 observation contamination |

The tested standalone object was therefore approximately “short-term residual reversal conditional on V3 observation selection,” not pure short-term residual reversal over its own broad eligible pool. No corrected return is calculated in this audit.

## Finding 5 — independent verifier gaps

The verifier checked uniqueness of ISO weeks among recorded decisions and checked that the reported validation dates lie inside the frozen boundary. Both checks can pass with zero decisions across several years.

Important invariants not checked:

- `minimum or expected weekly opportunity count` — No schedule is reconstructed and no expected count is compared with recorded decisions.
- `decision continuity through validation` — The verifier only checks uniqueness of recorded weeks, so an empty multi-year tail passes.
- `last decision date near the data end` — No recency or maximum-gap assertion exists.
- `nonzero validation rebalance activity` — Validation boundary checks reported dates, not decision or fill counts.
- `observation completeness skip rate` — Skipped decisions are not persisted as attempts or counted.
- `terminal lifecycle correctness` — The verifier hashes the lifetime file but never checks engine wiring or inactive managed pins.
- `realized sleeve attribution after position truncation` — Only merged target caps and aggregate exposure are checked; source ownership is absent.
- `strategy-specific observation-set identity` — No assertion compares the run strategy with the persisted observation_strategy_name.

The independent verifier returned `PASS` because hashes, signal probes, fill arithmetic, caps, NAV identities and date boundaries were internally consistent. Those checks do not establish active validation, lifecycle correctness, sleeve attribution, or strategy-owned observation identity.

## Decision-coverage table

Schedule denominator: first completed bar of each ISO week after the common 274-bar warmup, reconstructed from the existing full curve. No new returns are generated.

| Strategy | Period | Scheduled weeks | Recorded decisions | Fills | Last decision | Last fill | Coverage % |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: |
| V3 | development | 256 | 97 | 392 | 2018-12-17T05:00:00+00:00 | 2018-12-18T05:00:00+00:00 | 37.89 |
| V3 | validation | 239 | 0 | 0 | n/a | n/a | 0.00 |
| Reversal | development | 256 | 252 | 1360 | 2021-12-13T05:00:00+00:00 | 2021-12-14T05:00:00+00:00 | 98.44 |
| Reversal | validation | 239 | 0 | 0 | n/a | n/a | 0.00 |
| Treatment | development | 256 | 97 | 459 | 2018-12-17T05:00:00+00:00 | 2018-12-18T05:00:00+00:00 | 37.89 |
| Treatment | validation | 239 | 0 | 0 | n/a | n/a | 0.00 |

Annual structural coverage:

| Strategy | Year | Scheduled weeks | Recorded decisions | Fills | Coverage % |
| --- | ---: | ---: | ---: | ---: | ---: |
| v3 | 2017 | 47 | 47 | 219 | 100.00 |
| v3 | 2018 | 53 | 50 | 173 | 94.34 |
| v3 | 2019 | 52 | 0 | 0 | 0.00 |
| v3 | 2020 | 52 | 0 | 0 | 0.00 |
| v3 | 2021 | 52 | 0 | 0 | 0.00 |
| v3 | 2022 | 52 | 0 | 0 | 0.00 |
| v3 | 2023 | 52 | 0 | 0 | 0.00 |
| v3 | 2024 | 53 | 0 | 0 | 0.00 |
| v3 | 2025 | 52 | 0 | 0 | 0.00 |
| v3 | 2026 | 30 | 0 | 0 | 0.00 |
| reversal | 2017 | 47 | 47 | 241 | 100.00 |
| reversal | 2018 | 53 | 52 | 266 | 98.11 |
| reversal | 2019 | 52 | 51 | 315 | 98.08 |
| reversal | 2020 | 52 | 52 | 278 | 100.00 |
| reversal | 2021 | 52 | 50 | 260 | 96.15 |
| reversal | 2022 | 52 | 0 | 0 | 0.00 |
| reversal | 2023 | 52 | 0 | 0 | 0.00 |
| reversal | 2024 | 53 | 0 | 0 | 0.00 |
| reversal | 2025 | 52 | 0 | 0 | 0.00 |
| reversal | 2026 | 30 | 0 | 0 | 0.00 |
| treatment | 2017 | 47 | 47 | 237 | 100.00 |
| treatment | 2018 | 53 | 50 | 222 | 94.34 |
| treatment | 2019 | 52 | 0 | 0 | 0.00 |
| treatment | 2020 | 52 | 0 | 0 | 0.00 |
| treatment | 2021 | 52 | 0 | 0 | 0.00 |
| treatment | 2022 | 52 | 0 | 0 | 0.00 |
| treatment | 2023 | 52 | 0 | 0 | 0.00 |
| treatment | 2024 | 53 | 0 | 0 | 0.00 |
| treatment | 2025 | 52 | 0 | 0 | 0.00 |
| treatment | 2026 | 30 | 0 | 0 | 0.00 |

## Treatment attribution

The original artifacts are insufficient for historical pre-risk sleeve attribution. The corrected runner must persist raw component targets and source identity before the shared overlay. The deterministic synthetic proof above is the admissible evidence for the current implementation.

## Economic-result admissibility

The existing `RESEARCH_REJECT` may remain as a historical record of what the original code emitted. It may **not** be used as evidence that the frozen candidate economically failed. The admissible conclusion from this audit is implementation invalidity, not a strategy verdict.

## Minimal repair specification

1. Wire the hashed v5 lifetime metadata into the engine terminal map and emit deterministic lifecycle close/removal events before inactive managed symbols can block completeness.
2. Add fail-fast expected-week, minimum-coverage, maximum-gap and validation-activity assertions; separate passive carry, dividends and terminal cleanup from active validation.
3. Persist raw V3/reversal names, ranks, overlap, merged pre-cap targets, post-cap survivors, sleeve exposures and unused intended sleeve on every treatment decision.
4. Make the six-position policy sleeve-aware and deterministic under the frozen 75/25 allocation; preserve source rank/identity or use another predeclared account-level rule.
5. Build V3-specific, reversal-specific, and predeclared treatment observation sets from the broad eligible pool.
6. Strengthen the independent verifier to enforce all of the above before replay.

The next step is an implementation-corrected replay of the same frozen candidate. No parameter retune, gate change, or new strategy is proposed here.

## Files created

- `FORENSIC_AUDIT.md`
- `FORENSIC_AUDIT.json`
- `forensic_audit.py`
- `test_forensic_invariants.py`

## Commit SHA

Forensic artifact commit: `7e63668`. The final branch tip is reported in the task handoff.
