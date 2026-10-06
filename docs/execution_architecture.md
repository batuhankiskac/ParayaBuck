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
        └── alpaca_updates.py         AlpacaTradingUpdateStream: trade updates
        │
Alpaca TradingClient (REST)   ·   ExecutionLedger + PortfolioStateRepository (injected)
```

`alpaca.py` is the only public import (`AlpacaBroker`, `AmbiguousOrderState`,
`DefiniteOrderRejection`, `OwnershipDriftError`,
`ProtectionReconciliationError`, `RiskStatePersistenceError`). Services never
import the facade. The bot places no broker-side protective stops: VT35
decides once per session at the close and exits with ordinary SELLs.

## Modules

| Module | Responsibility |
|---|---|
| `alpaca_models.py` | `RiskStatePersistenceError`, `AmbiguousOrderState`, `DefiniteOrderRejection`, `ProtectionReconciliationError`, `OwnershipDriftError`. No broker calls |
| `alpaca_client.py` | Thin SDK adapter: exhaustive OPEN-order pagination, asset metadata (cached at most 24 h), DTO field normalization, session lifecycle |
| `risk_peak.py` | `data/runtime/paper_equity_peak.json` (`live_equity_peak.json` for live): peak equity, epoch, account binding, atomic writes. Missing → bootstrap 0; malformed, non-finite, negative or unreadable → `RiskStatePersistenceError` (fail-closed); account change → error. `reset_peak_equity` bumps the epoch |
| `alpaca_account.py` | Account + positions + portfolio-owned quantities → `AccountSnapshot`; blocked account → error; ownership `managed` / `mixed` / `external`; a short on a bot-traded (or owned) symbol → `OwnershipDriftError`; broker shortfall not explained by a working portfolio SELL → `OwnershipDriftError`; a foreign position the bot cannot claim tolerates a missing price. `fees` is the account's observed portfolio-fill fees. The snapshot cache is one `(snapshot, timestamp)` tuple |
| `alpaca_orders.py` | `PortfolioOrderRequest` → quantity-floored DAY `MarketOrderRequest` with deterministic `client_order_id`. Local guards (asset not tradable, below minimum size/notional, open order already exists) raise `DefiniteOrderRejection` before anything reaches the broker. A non-fractionable asset trades whole shares. On submit failure it looks the id up: working → recovered; terminal with a fill → the partial fill is booked and the terminal order returned; terminal/404 after a 4xx or insufficient-quantity refusal → `DefiniteOrderRejection`; otherwise terminal/404 → re-raise; lookup failure → `AmbiguousOrderState` (never a blind resubmit) |
| `alpaca_reconciliation.py` | Resolve durable nonterminal portfolio orders against the broker (`calculated` → `filled`/`done_for_day` by fill; `held`/`stopped`/`suspended`/`replaced`/`pending_review` stay working) → holdings buckets `MATCH`, `LOCAL_STALE`, `BROKER_STALE`, `QUANTITY_MISMATCH`, `UNKNOWN_ORDER`, `MISSING_LOCAL_ORDER`, `MISSING_BROKER_ORDER` on SIGNED broker quantities (a local open order the broker no longer lists is reported under both of the last two keys, a compatibility contract) → stale portfolio ownership cleanup → OPEN-order identity comparison. Idempotent. A local `submitting`/`reconciliation_required` order Alpaca returns 404 for is canceled once 10 min old. Crypto and foreign positions are left alone |
| `ledger.py` | Risk-state rows, persistent safety incidents (a corrupt row is kept with scope `corrupt`, never dropped), runtime sessions, and the read-only legacy ownership view. The legacy single-name tables are still created so old databases open, but nothing writes them. Opening a database whose `managed_positions` still has an `open`/`manual_intervention_required` row, or whose `orders` has a non-terminal row, raises `LegacyLedgerStateError` naming the rows and the manual close-out |
| `alpaca_updates.py` | Trade-update stream (primary fill source; REST fallback). A malformed event is logged and dropped; nothing raises into alpaca-py's `TradingStream` |

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
| Broker rejects (4xx/`rejected`), or a local guard refuses | `DefiniteOrderRejection` → row `rejected`, `order_rejected` | No (re-plan) | No | No |
| Submit fails, lookup finds a terminal order that partially filled | Fill booked, terminal order returned | No | No | No |
| Response malformed / no id | Error, no id fabricated | No | Yes | No |
| Account/positions/orders query fails | Error; no partial truth | No | Yes | No |
| Unknown order status | `ReconciliationResultError`, never filled/canceled | No | Yes | No |
| Short position on a bot-traded symbol | `OwnershipDriftError` | No | Yes | Yes |
| Open legacy ledger row at startup | `LegacyLedgerStateError` | No | Engine refuses to start | Yes |
| Broker holds less than managed, no working SELL explains it | `OwnershipDriftError` | No | Yes | Yes |
| Manual/unrelated open order | Does not excuse drift | No | Yes | Yes |
| Peak sidecar corrupt | `RiskStatePersistenceError` | No | Yes | Yes |
| Peak sidecar missing | Bootstrap 0 | — | No | No |

Retryability: transient network errors retry only where the broker proves
absence; permanent rejections are surfaced; ambiguity blocks and reconciles;
drift and risk-state errors halt entries until the operator acts. Execution
events carry `client_order_id`, `broker_order_id`, `symbol`, `market`, never
credentials.

## Tests

`tests/fake_client.py` (`FakeTradingClient`: deterministic, scriptable
faults) backs `tests/execution/` — characterization, fault matrix, chaos,
concurrency, order boundary/pagination, reconciliation repair and unknown
orders, terminal fill recovery, client-order salt.
