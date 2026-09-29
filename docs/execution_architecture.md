# Execution architecture

The Alpaca execution boundary (`src/parayabuck/execution/`). Goal: every
economic action is proven, recovered, or explicitly ambiguous — never guessed.

```
EngineRuntime / PortfolioRuntime
        │
AlpacaBroker facade (alpaca.py)
        ├── alpaca_models.py          errors, constants
        ├── alpaca_client.py          AlpacaClient: TradingClient adapter + normalization
        ├── risk_peak.py              RiskPeakService: peak-equity sidecar
        ├── alpaca_account.py         AccountService: account snapshot + ownership
        ├── alpaca_orders.py          OrderService: submission + recovery
        ├── alpaca_reconciliation.py  ReconciliationService: 7-bucket reconcile
        ├── alpaca_protection.py      ProtectionService: equity protective stops
        └── alpaca_updates.py         AlpacaTradingUpdateStream: trade updates
        │
Alpaca TradingClient (REST)   ·   ExecutionLedger + PortfolioStateRepository (injected)
```

`alpaca.py` is the only public import (`AlpacaBroker`, `AmbiguousOrderState`,
`OwnershipDriftError`, `ProtectionReconciliationError`,
`RiskStatePersistenceError`). Services never import the facade.

## Modules

| Module | Responsibility |
|---|---|
| `alpaca_models.py` | `RiskStatePersistenceError`, `AmbiguousOrderState`, `ProtectionReconciliationError`, `OwnershipDriftError`; stop-cancel timeouts. No broker calls |
| `alpaca_client.py` | Thin SDK adapter (account, positions, assets, orders, fill activities); SDK quirks and status mapping to `OrderState` stay here |
| `risk_peak.py` | `data/runtime/paper_equity_peak.json` (`live_equity_peak.json` for live): peak equity, epoch, account binding, atomic writes. Missing → bootstrap 0; malformed, non-finite, negative or unreadable → `RiskStatePersistenceError` (fail-closed); account change → error. `reset_peak_equity` bumps the epoch |
| `alpaca_account.py` | Account + positions + ledger ownership → `AccountSnapshot`; blocked account → error; ownership `managed` / `mixed` / `external`; broker shortfall not explained by a working managed SELL → `OwnershipDriftError` |
| `alpaca_orders.py` | `PortfolioOrderRequest` → quantity-floored DAY `MarketOrderRequest` with deterministic `client_order_id`. On submit failure it looks the id up: working → recovered, terminal/404 → re-raise, lookup failure → `AmbiguousOrderState` (never a blind resubmit) |
| `alpaca_reconciliation.py` | Fill-activity fee backfill (watermarked 24 h, paginated, deduped) → holdings buckets `MATCH`, `LOCAL_STALE`, `BROKER_STALE`, `QUANTITY_MISMATCH`, `UNKNOWN_ORDER`, `MISSING_LOCAL_ORDER`, `MISSING_BROKER_ORDER` → stale ownership / ledger cleanup → protective stops for ledger-managed or external equity positions only. Idempotent. A local `submitting`/`reconciliation_required` order Alpaca returns 404 for is canceled once 10 min old. Crypto positions are left alone |
| `alpaca_protection.py` | Equity protective stops: detect existing (incl. nested bracket legs), fix under/over-protection by cancel-confirm-replace, fractional DAY vs integral GTC. Portfolio-owned positions get no broker stop |
| `alpaca_updates.py` | Trade-update stream (primary fill source; REST fallback) |

Concurrency: no global broker lock; the ledger lock is never held across REST
calls; account snapshots are immutable and cached briefly.

## Failure semantics

When broker truth is uncertain: **do not guess.** Prefer `AmbiguousOrderState`,
`OwnershipDriftError`, `RiskStatePersistenceError` or a blocked rebalance →
entry halt / manual review over a silent retry.

| Condition | Result | Retry | Entry halt | Operator |
|---|---|---|---|---|
| Submit times out, lookup fails | `AmbiguousOrderState` | No (reconcile) | Yes | Maybe |
| Submit times out, lookup finds the order | Recovered broker id | — | No | No |
| Broker rejects (4xx/`rejected`) | Surfaced, `order_rejected` | No | Depends | No |
| Response malformed / no id | Error, no id fabricated | No | Yes | No |
| Account/positions/orders query fails | Error; no partial truth | No | Yes | No |
| Cancel unconfirmed / times out | `AmbiguousOrderState` | No | Yes | Yes |
| Unknown order status | `UNKNOWN` → ambiguous, never filled/canceled | No | Yes | No |
| Same symbol owned by ledger and repository | `OwnershipDriftError` | No | Yes | Yes |
| Broker holds less than managed, no working SELL explains it | `OwnershipDriftError` | No | Yes | Yes |
| Manual/unrelated open order | Does not excuse drift | No | Yes | Yes |
| Protection missing / over-sized | Re-protect remainder / cancel-replace | No | No | No |
| Peak sidecar corrupt | `RiskStatePersistenceError` | No | Yes | Yes |
| Peak sidecar missing | Bootstrap 0 | — | No | No |

Retryability: transient network errors retry only where the broker proves
absence; permanent rejections are surfaced; ambiguity blocks and reconciles;
drift and risk-state errors halt entries until the operator acts. Execution
events carry `client_order_id`, `broker_order_id`, `symbol`, `market`, never
credentials.

## Tests

`execution/fake_client.py` (`FakeTradingClient`: deterministic, scriptable
faults) backs `tests/execution/` — characterization, fault matrix, chaos,
concurrency, order boundary/pagination, reconciliation repair and unknown
orders, terminal fill recovery, client-order salt.
