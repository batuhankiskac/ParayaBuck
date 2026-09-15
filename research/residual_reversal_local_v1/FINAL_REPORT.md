# Final report: equity short-term residual reversal v1

## Decision

`RESEARCH_REJECT`

## Baseline

Current local `main` SHA: `8123694a71d8fbb0e520f13b4e1692e932754e97`. Research branch: `research/residual-reversal-local-v1`. Campaign artifact commit: `4b6dd3c` (the final branch handoff commit is reported in the task handoff).
The incumbent is `equity_residual_momentum_v3`, daily signal, weekly rebalance, 0.55 maximum equity allocation, 252/21 formation/skip, 273-bar beta window, 63-bar residual-vol window, top_n=8, ivol q=0.70, inverse-vol sizing, 0.10 position cap, and SPY benchmark. Current risk is gross 0.60, cash buffer 0.40, six positions, 0.5% daily loss and 5% drawdown limits; LIVE is disabled.

## Frozen candidate

`equity_short_term_residual_reversal_v1`: US equities, 1d, SPY residualization copied from V3, 273 aligned-return beta window, exactly 21 completed residual returns, zero skip, no positive-score gate, worst residual performers ranked strongest, long-only, weekly, top_n=6, V3 ivol q=0.70, V3 63-bar residual-vol inverse weighting, and 0.10 per-position cap. No extra indicators or tuning were used.

## Historical dataset

| Item | Result |
| --- | --- |
| Source | local Alpaca provider-identity v5 panel |
| Feed | sip |
| Adjustment | split |
| Period | 2016-01-04T05:00:00+00:00 to 2026-07-31T04:00:00+00:00 |
| Rows | 11,686,832 |
| Symbols/entities | 9,010 / lifetime table per receipt |
| PIT quality | broad provider-identity active/inactive panel; limitations retained |
| SHA-256 | bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139 |

The panel had 0 explicit all-zero placeholders, 0 invalid positive OHLC rows, and 0 duplicate symbol/timestamp rows. SPY covered 2,658 sessions from 2016-01-04 through 2026-07-30; the panel has a final non-SPY session on 2026-07-31.
Cash-dividend source: `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`; 141,905 positive cash-dividend actions were applied through the run end. Split/stock/merger actions were not reapplied to split-adjusted prices.

## Standalone reversal

| Cost x | Net return | CAGR | Vol | Sharpe | Sortino | Max DD | Calmar | PF | Turnover | Fills | Rebalances | Avg exposure | Max concentration |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.6511 | 0.0538 | 0.0783 | 0.7078 | 0.9072 | 0.1331 | 0.4038 | 1.5734 | 104.0269 | 1360 | 252 | 0.426 | 0.207 |
| 2 | 0.5977 | 0.0502 | 0.0783 | 0.6639 | 0.8511 | 0.1381 | 0.3631 | 1.5236 | 104.8709 | 1360 | 252 | 0.426 | 0.207 |
| 3 | 0.5457 | 0.0465 | 0.0784 | 0.6197 | 0.7941 | 0.1431 | 0.3252 | 1.4749 | 105.7182 | 1360 | 252 | 0.427 | 0.208 |

## V3 control

Fresh same-data 1x V3: net return 0.2172, CAGR 0.0207, volatility 0.0748, Sharpe 0.3121, Sortino 0.3704, max drawdown 0.2154, Calmar 0.0963, turnover 31.8530, costs 1162.20. V3 cost-stress results are in `v3_control.json`.

## Diversification

Daily return correlation: 0.4567 over 2407 observations; monthly correlation: 0.3885. Drawdown-day Jaccard: 0.8988; both-negative months: 26. Mean decision-date holdings Jaccard: 0.1174; mean minimum-weight overlap: 0.1011.
Monthly diagnosis counts: V3 down/reversal up 17, reversal down/V3 up 19, both down 26.

## Capital-neutral treatment

Control is 0.55 V3 plus cash. Treatment is 0.4125 V3 + 0.1375 reversal plus cash. Targets were combined symbol-by-symbol in one engine ledger, then the unchanged six-position/per-position/gross/cash/order overlay was applied.
| Cost x | Net return | CAGR | Sharpe | Sortino | Max DD | Calmar | Turnover | Max concentration |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.1783 | 0.0173 | 0.4842 | 0.5769 | 0.0944 | 0.1832 | 23.8864 | 0.138 |
| 2 | 0.1694 | 0.0165 | 0.4625 | 0.5506 | 0.0944 | 0.1746 | 23.9610 | 0.138 |
| 3 | 0.1605 | 0.0157 | 0.4409 | 0.5241 | 0.0944 | 0.1660 | 24.0358 | 0.138 |

## Development vs validation

| Run | Window | Net return | CAGR | Sharpe | Sortino | Max DD | Turnover |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V3 | development | 0.1058 | 0.0203 | 0.2942 | 0.3514 | 0.2154 | 33.3158 |
| Reversal 1x | development | 0.3330 | 0.0592 | 0.6738 | 0.8504 | 0.1021 | 110.6357 |
| Treatment 1x | development | 0.1443 | 0.0274 | 0.6705 | 0.8291 | 0.0827 | 24.6080 |
| V3 | validation | 0.1007 | 0.0212 | 0.3357 | 0.3965 | 0.1883 | 0.0000 |
| Reversal 1x | validation | 0.2387 | 0.0479 | 0.8020 | 1.1124 | 0.1224 | 0.0000 |
| Treatment 1x | validation | 0.0298 | 0.0064 | 0.2234 | 0.2517 | 0.0944 | 0.0000 |

## Turnover and costs

The full 1x/2x/3x cost-stress table is above and the complete control/treatment stress JSON is retained. Gross-minus-net return and fill-level slippage, spread, and regulatory fees are separately recorded in each result.

## Concentration / robustness

The treatment robustness record is in `robustness.json`. It covers calendar years, trade contribution concentration, largest drawdown periods, independent filled symbols/trades, and a realized-PnL leave-one-dominant-symbol-out diagnostic that does not modify or rerun the strategy. Treatment 1x maximum concentration was 0.1384.

## Independent verification

Separate verifier status: `PASS`. It checked protocol/data hashes, normalized-row invariants, synthetic 21/zero-skip signal semantics, V3 source identity, weekly decisions, next-bar fills, cost arithmetic, target caps, shared allocation, NAV arithmetic, and validation boundaries.

## Frozen gate table

| Gate | Status | Evidence |
| --- | --- | --- |
| standalone viability | PASS | {"full_cagr": 0.053763118366780605, "validation_return": 0.2386765813382712} |
| cost robustness | PASS | {"2x_net_return": 0.5976873245677548, "3x_net_return": 0.545743650730043} |
| diversification | PASS | {"daily_correlation": 0.4567105138570184} |
| capital-neutral portfolio contribution | PASS | {"improvements": {"cagr": false, "calmar": true, "sharpe": true, "sortino": true}, "non_improving_metrics_within_2pp": true} |
| drawdown | PASS | {"control_max_drawdown": 0.21535135655629245, "treatment_max_drawdown": 0.09436739482312821} |
| validation contribution | FAIL | {"control_validation": {"cagr": 0.021200004425627528, "max_drawdown": 0.18830099345793383, "sharpe": 0.3356973007790247}, "treatment_validation": {"cagr": 0.006436581166965771, "max_drawdown": 0.09436739482312821, "sharpe": 0.22342150267837346}} |
| turnover economics | PASS | {"control_cost_drag": 0.01237873660570532, "control_turnover": 31.85296924739919, "treatment_cost_drag": 0.008908385319741363, "treatment_turnover": 23.886439088868784} |
| concentration | PASS | {"interpretation": "qualitative gate operationalized by breadth, contribution share, and leave-one diagnostic", "largest_absolute_symbol_share": 0.06356999815935481, "leave_one_approximate_return": 0.15344172720497995, "maximum_realized_weight_concentration": 0.13842227385387468, "unique_filled_symbols": 94} |

## Final reasoning

The exact verdict is `RESEARCH_REJECT` because it follows the frozen gates above; no parameter, allocation, universe, or production setting was changed after observing results. This result is research-only and does not register the candidate for PAPER or alter production.

## Files created

- `CURRENT_BASELINE.md`, `FROZEN_PROTOCOL.md`, `DATA_RECEIPT.json`, `run_manifest.json`
- `candidate.py`, `run_campaign.py`, `verify_results.py`, `test_candidate.py`
- `universe_snapshot.json`, `standalone_reversal.json`, `v3_control.json`, `treatment.json`, `cost_stress.json`, `diversification.json`, `robustness.json`, `execution_audit.json`, `independent_verification.json`
- `FINAL_REPORT.md`
