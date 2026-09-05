# high_proximity364 final assessment

**Date:** 2026-09-05  
**Status:** research assessment; no production, PAPER/LIVE, configuration, trading or promotion authorization

## Decision

`high_proximity364` should not be integrated into the trading product, used as an entry veto, or combined with the incumbent in a live/shared execution architecture. The corrected replay is technically valid and supports only further passive shadow validation after a genuinely new holdout and stronger venue/tradability evidence. It does not demonstrate a reliable incremental portfolio benefit.

The result is `SUPPORTS FURTHER SHADOW VALIDATION` for the research observer role and `NO_INCREMENTAL_BENEFIT_DEMONSTRATED` for product integration. These are separate conclusions: the corrected signal is worth observing, but the available retrospective evidence is insufficient for actuation.

## What was corrected

The original screen was not a valid implementation of the intended 364-price strategy. `HighProximity364Adapter` requested a 364-bar window but inherited a 23-bar minimum-history declaration. The native engine trims ordinary symbols to `max(minimum_history_bars, 274)` while exempting benchmark `BTC/USD`. The original trace confirmed the resulting causal failure: BTC received enough visible history, while ordinary symbols did not. The old path therefore produced BTC-only behavior through a structural history asymmetry.

The corrected research adapter changes only the declared minimum history to 364. It keeps the score, current-close inclusion, HYST4 retention, top-2 equal weight, 96h cadence, 5% crypto cap, daily +24h signal shift, next-open fill, costs, risk limits and terminal policy unchanged. It lives in `scripts/research/high_proximity364_history_fixed_2026_09_05.py`; the separate six-cell runner and manifest are frozen in the corresponding research paths. No production registry, YAML, V3 source or frozen screen source was changed.

The original 42-cell accounting audit remains true for the artifacts it audited: cash reconciliation, finite metrics, fills and terminal marking passed. It did not test semantic adequacy of a 364-bar candidate whose inherited history declaration was too short. That is why the original accounting `PASS` does not validate the original P364 algorithm. The original P364 results are retained as `INVALID_ALGORITHM_BASELINE_HISTORY_TRIM`, not silently rewritten.

## Corrected six-cell evidence

The corrected run used the pinned input through 2026-07-31, full 2022-01-01..2026-07-31 and recent 2024-01-01..2026-07-31 windows, and 1x/2x/3x cost multipliers. Full and recent are overlapping retrospective slices selected after the 16-heading screen; neither is a fresh out-of-sample holdout.

| Window | Cost | Native hourly TR | Native hourly max DD | Hourly Sharpe | Fills | Fees |
|---|---:|---:|---:|---:|---:|---:|
| full | 1x | 6.8686% | 2.798% | 0.693 | 1104 | 166.94 |
| full | 2x | 6.6800% | 2.820% | 0.675 | 1104 | 333.61 |
| full | 3x | 6.4917% | 2.843% | 0.657 | 1103 | 500.02 |
| recent | 1x | 1.6306% | 2.801% | 0.322 | 995 | 126.89 |
| recent | 2x | 1.4911% | 2.824% | 0.296 | 994 | 253.65 |
| recent | 3x | 1.3517% | 2.846% | 0.269 | 993 | 380.28 |

The corrected candidate traded AAVE, AVAX, BCH, BTC and ETH. This is a material semantic change from the invalid original path's 68 full-window fills and BTC-only exposure, not a parameter improvement. The corrected audit reports exact cash-plus-marked-position reconciliation and finite outputs for all six cells. Across the corrected candidate cells, active exposure was 60.50% of full-window hours and 61.43% of recent-window hours, with mean gross exposure 2.981% and 2.386%, respectively. The run records 129 incomplete-batch warning lines: 41 full-window timestamps × 3 costs plus 2 recent-window timestamps × 3 costs, not 129 independent events. Those warnings remain evidence about the causal observation path; they are not silently converted into alpha or cash decisions, and detailed per-decision observer tracing remains a limitation for stronger conclusions.

The original and corrected fills should be read as a before/after validity check: 68 → 1,104 full-window 1x fills, and BTC-only → five actual symbols; corrected recent cells contain 993–995 fills. The difference demonstrates that the history declaration affected opportunity visibility; it does not establish that the new five-symbol behavior is profitable out of sample.

## Period-level complementarity

At 1x full-window native equity curves, the corrected candidate's calendar-year returns were approximately -0.37% (2022), +5.19% (2023), +2.49% (2024), +0.28% (2025), and -0.78% (2026 through July). The reused incumbent's corresponding returns were -3.80%, +4.58%, +3.43%, +1.45%, and -1.65%. The candidate helped most in the 2022 recovery and 2023 period, while the incumbent led in 2024–2026. This is descriptive chronological evidence, not a post-hoc regime switch or a claim that either signal owns a regime.

The audited 50/50 initial-capital curve mixes are diagnostic only. Full-window mix returns were 5.35%, 4.80% and 4.26% at 1x/2x/3x, versus incumbent returns 3.83%, 2.93% and 2.04%; mix max drawdowns were 2.94%, 3.02% and 3.10%, versus incumbent 5.17%, 5.20% and 5.25%. Recent mix returns were 1.87%, 1.49% and 1.11%; the 1x mix was below the incumbent, 2x was effectively tied, and 3x was above it. Mix daily net-PnL correlations were about 0.928–0.938. Every mix had worse max drawdown than its corresponding half-cash control: full 1x was 5.35% TR/2.94% DD versus half-cash 1.91%/2.59%; recent 1x was 1.87%/2.98% versus 1.06%/1.80%. Hourly native TR/DD/Sharpe and daily correlation are kept separate in the audit.

The mix evidence shows potential portfolio complementarity in the retrospective curve, especially over the full period, while also showing that the mixed drawdown remained worse than half-cash. It is not shared execution and does not establish continuous rebalancing, order competition, netting, max-two crypto enforcement under two independent target lists, or full equity-V3 account behavior. The activity and cash timing matter: the candidate's old BTC-only path stopped after 2025-07-03, and the exposure trace identifies a raw daily-data gap around 2025-06-28 followed by the 2026-03-12 gap, preventing 364-day recovery. This is a data/history fragility finding, not a regime signal. The candidate remains research-worthy for complementarity; the evidence does not support actuation.

## Integration architecture

The passive observer is the only supported next role. It can record causal observation membership, 364-readiness, ranks, targets, hypothetical fills, active hours, selected symbols and concentration without affecting orders or account state. A new post-2026-07-31 holdout and venue/tradability checks would be needed before reconsidering any stronger role.

An ensemble remains unproven. Independent incumbent and candidate top-2 lists can union to three or four names while the contract permits only two crypto positions. A joint allocator would either drop targets, convert them to cash, or change the cap. The diagnostic 50/50 curve mix does not solve this. A future research-only ensemble would need one common eligible pool, one frozen equal-rank rule, exactly top-2 selection, shared cash and deterministic collision handling; it should be considered only after the candidate survives an unseen holdout.

An entry veto is not supported. The candidate's prolonged cash periods and BTC/coin concentration could suppress incumbent entries without proving that the veto adds value. It would require separate missed-entry accounting and a causal rule, neither of which is established here.

## Limits and next evidence

The available data ends 2026-07-31, so no post-cutoff holdout was available. The 1h OHLCV source supplies modelled costs and scanner proxies, not a complete venue-point-in-time bid/ask history. Several native decisions are recorded as incomplete observation batches and skipped; those are included in the audit and must not be interpreted as economic cash signals until the observer trace explains them. The score's academic motivation is related to proximity-to-high/52-week-high continuation evidence, such as [George and Hwang, 2004](https://doi.org/10.1111/j.1540-6261.2004.00627.x), but that literature does not validate this crypto universe, history availability, cost model or portfolio contribution.

The next scientifically useful evidence is a frozen observer trace on the corrected path plus a genuinely unseen post-cutoff dataset with enough history and at least 90 completed decisions after warm-up. It should compare corrected candidate, incumbent on a matched readiness mask, native BTC and cash, with causal coverage and selected-symbol receipts. No parameter, threshold, cadence, blend-weight or regime-selection search is justified.

## Receipts

Corrected output: `data/research/high_proximity364_history_fixed_2026_09_05/results.json`  
Output SHA-256: `73783d27c6b8ace01a7571844ef56817f771b0751060fdc45ad9d702684359f3`  
Independent audit: `data/research/high_proximity364_history_fixed_2026_09_05/audit_2026_09_05.json` (SHA-256 `9cd1653b626870edc5efc90c04f93c6fc3e784e49afce1bfe73d8afae7203f1d`)  
Launch session: `5877`, exit code `0`  
Manifest raw SHA-256: `087827408096a392d603b3c1f97f001b6c703c88a8b71231b17c99c286ebebca`  
Manifest canonical digest: `2e391f735055d43af7903536e3f6ef2636b9080c343b36e3bea045d2e7784ed0`
