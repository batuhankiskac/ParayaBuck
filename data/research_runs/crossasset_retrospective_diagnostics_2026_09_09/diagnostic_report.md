# Crossasset V2 retrospective diagnostics — 2026-09-09

Frozen before outcomes. All signals are retrospective non-PIT diagnostics; missing availability, valuation vintage, borrow, margin, settlement, revision, or executable fill evidence prevents admission.

Protocol SHA: `861eb1e011b05880226dbd37399359dd2dd548bcfd510cc6bd7c6fd7afdef0bd`  
Results SHA: `decdb1e18cdde30532b2735b1a53a628d57ae026a1b68cf5b486976e10b34563`  
Overlay: `NOT_RUN_NO_ELIGIBLE_CANDIDATE`; economic run: `false`; admitted rows: `0`.

| Card | Status | Signal rows | Full/recent/holdout | Admitted |
|---|---|---:|---|---:|
| X01 | RETROSPECTIVE_NON_PIT_DIAGNOSTIC | 2645 | 2645/645/395 | 0 |
| X02 | PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN | 0 | 0/0/0 | 0 |
| X03 | DATA_BLOCKED_PIT_VALUATION_FIELDS_MISSING | 0 | 0/0/0 | 0 |
| X04 | RETROSPECTIVE_NON_PIT_DIAGNOSTIC | 2 | 2/2/0 | 0 |
| X05 | DATA_BLOCKED_CONTINUOUS_PIT_OI_UNAVAILABLE | 0 | 0/0/0 | 0 |
| X06 | RETROSPECTIVE_NON_PIT_DIAGNOSTIC | 372 | 372/372/186 | 0 |

Cost, turnover, drawdown/tail, correlation, bootstrap, permutation/placebo and two-leg execution are recorded as not run or blocked where their required outcome/leg inputs are absent. No OHLCV or current snapshot is used as a causal substitute.
