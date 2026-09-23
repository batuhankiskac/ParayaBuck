# Startup decision barrier

`EngineRuntime._start_impl()` invokes `ReconciliationReport.from_result(broker.reconcile())` before portfolio runtime construction and startup catch-up. Any exception or non-MATCH drift halts startup. The existing `_startup_committed` gate remains false until startup completes. This repair makes targeted order recovery and complete FILL traversal mandatory steps inside `broker.reconcile()`. Failure cannot become an empty order/activity result or permit normal entry decisions. No new independent flag is needed.
