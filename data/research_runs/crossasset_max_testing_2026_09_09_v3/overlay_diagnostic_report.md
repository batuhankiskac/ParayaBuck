# Crossasset maximum-testing V3 diagnostics — 2026-09-09

Fixed formulas and grids were frozen before outcomes. Coin Metrics PriceUSD and the local Yahoo ETF bars are retrospective outcomes only; all source rows lack `available_at`, so admission and economic eligibility remain false.

Protocol SHA: `f9989dcbefbe0c4699e94db214365cfeebee60a9e39ce740c5f8397ae7a5be6f`  
Results canonical SHA: `c489e27670bc6fab74d91c38063166525f962ec407c1ea9c4c55bc89f9ec96aa`  
Arms: `162` (3 measured cards × 2 overlay modes × 3 lags × 3 costs × 3 windows).

| Card | Signal rows | Fixed/capacity arms | Status |
|---|---:|---:|---|
| X01 | 2645 | 54 | RETROSPECTIVE_NON_PIT_DIAGNOSTIC |
| X02 | 0 | 0 | PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN |
| X03 | 0 | 0 | DATA_BLOCKED_PIT_VALUATION_FIELDS_MISSING |
| X04 | 0 | 0 | DATA_BLOCKED_BORROW_HISTORY_UNAVAILABLE |
| X05 | 576 | 54 | RETROSPECTIVE_NON_PIT_DIAGNOSTIC |
| X06 | 39 | 54 | RETROSPECTIVE_NON_PIT_DIAGNOSTIC |

Capacity-aware arms preserve V3 and allocate only spare capacity; fixed 95/5 arms report cap violations where present. No arm changes V3, admission, promotion, or production state.
