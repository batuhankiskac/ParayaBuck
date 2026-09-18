# Reserved Equity Role Defect Receipt

Audit phase: pre-fix structural reproduction  
Repository: `/Users/batuhankiskac/Desktop/ParayaBuck`  
Baseline: `main @ 5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`  
Maintenance branch: `maintenance/reserved-equity-role-isolation-v1`

## Fixture

The deterministic V3 fixture reuses the existing `tests/test_equity_residual_momentum_v3.py` synthetic paths. It adds `SH`, `PSQ`, and `DOG` with the same positive path as `AAA`, keeps `SPY` as the benchmark, and makes all four reserved symbols available in the fake point-in-time universe. The current production parameters are unchanged and contain only `SPY` in `exclude_symbols`.

The pre-fix probe was:

```text
PYTHONPATH=tests .venv/bin/python -c '... _production_v3().rank_candidates(context) ... _production_v3().decide(context) ... build_observation_set(... pinned_symbols=("SPY","SH","PSQ","DOG")) ...'
```

## Observed pre-fix behavior

```text
ranked_reserved=('DOG', 'PSQ', 'SH')
target_reserved=('DOG', 'PSQ', 'SH')
observation_set=('SPY', 'SH', 'PSQ', 'DOG', 'AAA', 'DDD')
reserved_present=True
```

This proves the defect without using P&L:

* `SPY` is excluded from V3 ranking/targets by the existing benchmark exclusion.
* `SH`, `PSQ`, and `DOG` remain available in the reserved observation set.
* The V3 scoring/target path has no role-level prohibition for those inverse hedge symbols, so they can enter ordinary V3 ranking and targets when present in the runtime context.

The fix must therefore remove only ordinary-alpha eligibility for configuration-reserved roles while preserving their observation, history, streaming, and managed-position paths.
