# RR-01 pre-fix reproduction

The audit pinned at `3551322b9ac609ed2ee52c0343a14f2dcd408f37` reproduced this through `test_filled_buy_without_trade_update_halts_before_phase_recovery`. Before this repair, the new regression `test_full_buy_fill_while_offline_reconciles_before_drift` also failed on clean-main production code: local `submitted` BUY, broker terminal `filled`, broker OPEN empty, broker position 1. Startup compared ownership before looking up the terminal order and reported drift. A SELL and partial-fill variant exposed the same ordering issue. Three new RR-01 regressions failed before production edits.

Failure mechanism: `ReconciliationService.reconcile()` fetched `account()` (which classified broker position against stale local ownership) before recovering the durable nonterminal order. `EngineRuntime._start_impl()` treated the resulting drift as fatal, so later portfolio phase recovery could not run.
