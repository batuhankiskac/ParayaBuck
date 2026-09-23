# PAPER/LIVE production call graph

1. `EngineRuntime` creates equity and crypto `PortfolioRuntime` instances
   sharing the broker, repository, and risk limits (`runtime.py:945-991`). Its
   timestamp, startup and restart paths order simultaneous markets crypto
   first (`runtime.py:2482-2508, 3073-3120, 3537-3567`). A shared account
   snapshot and earlier market's actual planned BUY keys cross the boundary.
2. `AlpacaBroker.account()` calls `get_all_positions()`, identifies market
   and canonical symbol, rejects duplicate keys, and reports managed and
   external quantities (`execution/alpaca.py:403-522`).
   `all_open_order_symbols()` calls the adapter's exhaustive OPEN-order query
   (`execution/alpaca.py:546-565`); the pagination fix is in
   `execution/alpaca_orders.py`.
3. `PortfolioRuntime.process_closed_bars()` constructs the strategy context,
   calls `strategy.decide()`, then `PortfolioRiskOverlay.apply()` with the
   account, nonterminal repository order keys, broker open symbols, and
   same-cycle reservations (`portfolio/runtime_service.py:699-1029`). Equity
   V3 and crypto hysteresis generate targets in their respective strategy
   classes; neither submits an order.
4. The overlay trims account-global and market target slots before sleeve,
   gross and per-asset scaling (`execution/portfolio.py:265-415`).
   `TargetToOrderPlanner.plan()` compares target and current weights, applies
   the rebalance band, minimum size/notional, ownership and open-order guards,
   then emits SELLs first and BUYs in symbol order
   (`execution/portfolio.py:425-553`).
5. The account-level `reserve_opening_order_slots()` now counts every actual
   nonzero position and known working slot before admitting immediate BUYs,
   reserving each new name in planner order. An actual SELL causes the
   existing two-phase path: persist and submit SELLs, hold BUYs, reconcile
   terminal outcomes, refresh the account, reapply risk, replan, then apply
   the same slot check before BUY submission (`portfolio/runtime_service.py:
   1130-1208, 1210-1470`).
6. `_submit_orders()` persists submitting rows before broker calls, uses
   deterministic client IDs, and records outcomes. Trade updates and
   reconciliation update order status/positions; nonterminal local rows
   continue reserving slots (`portfolio/runtime_service.py:1473-1665`,
   `portfolio/runtime_repository.py:810-839`, `runtime.py:2510+`).

Risk counts actual broker positions for occupied capacity, desired names for
target selection, and working orders for reservations. Intended exits do not
release occupied capacity at the admission boundary. Broker-visible and
persisted orders are deduplicated by canonical market/name. Sequential BUY
admission prevents two intents spending one remaining slot.
