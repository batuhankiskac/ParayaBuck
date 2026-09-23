# Startup sequence

## Before

`EngineRuntime` constructs durable portfolio repository and ledger, configures broker callbacks, then `start()` invokes `broker.reconcile()`. In reconciliation, `broker.account()` fetched account and positions and classified ownership using stale local order/position state; OPEN orders and FILL backfill were consulted later. Drift halted startup before portfolio runtime restoration, risk restore, warmup, rebalance resume, and first decision. A failed FILL request could be treated as deferred.

## After

`broker.reconcile()` binds account identity, exhaustively lists OPEN orders, reads durable nonterminal orders, resolves each against OPEN or targeted order lookup, and applies authoritative cumulative status/fills. It then fully stages and validates FILL history, applies it, and only then calls `broker.account()` to classify positions against reconciled ownership. Existing ownership/ledger drift and protection checks run; the final OPEN comparison refetches exhaustive orders after protection restoration. `EngineRuntime` rejects any reconciliation exception or drift before constructing portfolio runtimes, restoring risk/strategy state, warmup, rebalance resume, or startup catch-up. Its existing startup barrier remains in force.

The repository's `account()` call fetches account and positions together; the initial account-ID fetch precedes scoped durable reads. This is the actual ordering used rather than a separate position fetch before order recovery.
