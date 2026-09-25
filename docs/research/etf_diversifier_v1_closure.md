# ETF diversifier v1 — closure (2026-09-25)

Decision: **STAGE_B_FAIL — no production change; shipped V3 0.55 + crypto 0.05
configuration stays.** Full evidence (pre-registrations, code, data manifest,
curves, shared-ledger run outputs) is preserved at annotated tag
`archive/research/etf-diversifier-v1-stage-b-fail-2026-09` (commit
`36b99e53bd1e6ccb989a401e7668fd4796fba74e`); only this summary is on main.

## Question

Does a cross-asset ETF sleeve (15% of the account, funded from V3 0.55 → 0.40)
improve the combined account across regimes? Frozen candidates:

- **C1 static:** SPY / TLT / GLD / BIL, 25% each, monthly.
- **C2 trend:** SPY / TLT / IEF / GLD / DBC, each 1/5 when its 12-month return
  beats BIL's, otherwise that fifth sits in BIL; monthly.

Data: Alpaca SIP daily split-adjusted bars plus explicit cash dividends,
2016-01 → 2026-07; evaluation 2017-01-03 → 2026-07-30 (development ≤ 2021,
validation ≥ 2022), costs at 1x / 2x / 3x.

## Stage A — separate sleeves summed (screen)

Both candidates passed all eight gates (C1 Sharpe 0.757, C2 0.749 vs control
0.657). A post-hoc matched cash control (V3 0.40 + idle cash) showed that the
drawdown and 2022 improvements came mostly from holding less V3; that control
was therefore made mandatory in Stage B.

## Stage B — one shared account (confirmation)

Accepted physical-clock shared-ledger harness: one cash balance, account-wide
0.5% daily-loss latch, cap 8 (ETF holdings as additive slots). An invariance
arm reproduced the shipped run's decision and fill hashes exactly; accounting,
slot and fill-timing gates held in all 12 runs.

| Arm (1x) | Total | CAGR | Sharpe | Max DD | Val Sharpe | 2022 |
|---|---:|---:|---:|---:|---:|---:|
| A shipped: V3 0.55 + crypto | 85.8% | 6.69% | 0.675 | 19.4% | 0.475 | −15.3% |
| B cash control: V3 0.40 + cash + crypto | 61.8% | 5.15% | 0.677 | 17.3% | 0.446 | −13.1% |
| C1 static | 73.3% | 5.91% | 0.724 | 19.1% | 0.444 | −14.9% |
| C2 trend | 74.6% | 6.00% | 0.742 | 17.3% | 0.557 | −13.3% |

- **C2** failed only the CAGR guard (−0.69 pp/year vs the −0.50 limit).
- **C1** also failed the Sharpe-uplift, validation and cash-control gates.
- ETF P&L was positive in every cost case (C1 $14–15k, C2 $12.5–13.5k per
  $100k), so the sleeve is a genuine diversifier, but a lower-returning one than V3.

## Lessons for successors

- Always include a matched cash control: de-risking alone lowers drawdown.
- Summed-sleeve screens overstate shared-account results: C1's CAGR gap doubled
  (−0.39 → −0.78 pp) through daily-loss-latch and gross-ceiling path dependence.
- Re-tuning these sleeves on the same 2017–2026 window to clear G3 is excluded
  as data mining; a successor needs a genuinely different hypothesis.
