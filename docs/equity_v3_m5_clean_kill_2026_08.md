# M5 Shrinkage ERC — Clean Kill Check

Date: 2026-08-31  
Status: exact predeclared research identity rejected; no full robustness run.

The check was run from a clean tracked checkout at source commit
`16c9426f728f24f44bd86020854550cd0cbfde50`, with the current-parity control
and M5 using the same canonical data, T1 terminal policy, costs, observation
universe and `0.55` equity allocation. Raw output is retained under the
gitignored `data/research/equity_v3_followups_2026_08/` directory.

## Receipt

| Field | Value |
| --- | --- |
| Runner | `scripts/research_v3_equity_followups.py --missions m5` |
| Runner SHA-256 | `4f8ca46ceb1b8bb5b23c660308d1769be465f19e1a1205c877cd2d622812568d` |
| Result SHA-256 | `7b9c00fdb4fa858834385a81528a60801bbdc15d195e608f09aa242af422f078` |
| Tracked dirty paths | `0` |
| Incomplete batches | `0` |
| Decisions | `495` |

## Same-run result

| Metric | Control | M5 ERC | Delta |
| --- | ---: | ---: | ---: |
| Total return | `76.4538%` | `76.6425%` | `+0.1887 pp` |
| Sharpe | `0.571342` | `0.602380` | `+0.031038` |
| Max drawdown | `20.3809%` | `19.1964%` | `-1.1845 pp` |
| Turnover | `149.724817` | `146.191627` | `-3.533190` |

The frozen promotion threshold is Sharpe `0.571342 + 0.040000 = 0.611342`;
M5 reaches `0.602380`, short by `0.008962`. M5 used the ERC path on all 495
decisions (`erc_decision_count=495`, `fallback_decision_count=0`,
`fallback_rate=0.0`).

Decision: reject the exact `equity_residual_momentum_shrinkage_erc_v1`
specification under the predeclared rubric. Do not run folds, bootstrap,
jackknife or post-result parameter changes for this identity. Any future
covariance research must be a new predeclared identity with estimator parity
and explicit fallback semantics.
