# Capacity-aware crypto diagnostics — 2026-09-09

This package is explicitly `POST_HOC_EXPLORATORY`. Current V3 NAV and decisions are preserved at 100%; candidates receive only dated spare capacity. Missing `available_at` keeps every result non-PIT and inadmissible.

Protocol SHA: `7a580df8e213aaf723dd9661a4213fff9e369d6460abe5993b4e48d0ecf7d55c`  
Results SHA: `5b217b17323acec96419d5bb22a3c5f8353b99d95a1d9c947712f11092338df8`  
Arms: `162`; cap violations: `0`.

| Branch | Signal rows | Arms | Status |
|---|---:|---:|---|
| K01 | 5538 | 27 | POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT |
| K03 | 5478 | 27 | POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT |
| K04 | 5536 | 27 | POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT |
| K11 | 1 | 27 | POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT |
| K09 | 2444 | 27 | POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT |
| K09_funding_short_history | 300 | 27 | POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT |

Representative full-window result (lag=1, cost=1x; all grid arms are machine-readable in `capacity_diagnostic_results.json`):
- K01: portfolio_cagr=0.0865416570439308, contribution_cagr=0.0007639850762501954, DD=-0.12825759070291531, CVaR05=-0.015012455819036404, active_days=49, turnover=1.3000000000000005, bootstrap=(-1.3791400235431573e-05,1.8167479831386125e-05), placebo_p=0.5581395348837209, cap_violations=0
- K03: portfolio_cagr=0.08198280849224338, contribution_cagr=-0.0034258094403307737, DD=-0.13007915702950057, CVaR05=-0.015060468652786775, active_days=126, turnover=4.749999999999992, bootstrap=(-4.656303472548092e-05,1.935249448949943e-05), placebo_p=0.8682170542635659, cap_violations=0
- K04: portfolio_cagr=0.08599008065902103, contribution_cagr=0.0002447336181929938, DD=-0.12975022247684453, CVaR05=-0.015060250568606099, active_days=172, turnover=3.699999999999995, bootstrap=(-4.1994416254538763e-05,3.3503189715049525e-05), placebo_p=0.5813953488372093, cap_violations=0
- K11: portfolio_cagr=0.08569482045328058, contribution_cagr=0.0, DD=-0.1272551231595922, CVaR05=-0.014965564577325926, active_days=0, turnover=0.0, bootstrap=(0.0,0.0), placebo_p=None, cap_violations=0
- K09: portfolio_cagr=0.08618631557390932, contribution_cagr=0.000461535485104525, DD=-0.1272551231595922, CVaR05=-0.014938939012006207, active_days=27, turnover=1.7500000000000009, bootstrap=(-1.1626555170790062e-05,1.622454358024799e-05), placebo_p=0.6434108527131783, cap_violations=0
- K09_funding_short_history: portfolio_cagr=0.08569482045328058, contribution_cagr=0.0, DD=-0.1272551231595922, CVaR05=-0.014965564577325926, active_days=0, turnover=0.0, bootstrap=(0.0,0.0), placebo_p=None, cap_violations=0
