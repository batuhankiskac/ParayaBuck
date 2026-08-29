# Execution Architecture — Mission 7

> **Source:** `src/parayabuck/execution/alpaca.py` — 2872 lines, 139856 bytes, HEAD a731517
> **Goal:** Make the Alpaca execution boundary reviewable, testable, and safely modifiable without changing trading semantics.

## 1. Overview

```
PortfolioRuntime / EngineRuntime
        │
        ▼
AlpacaBroker facade (src/parayabuck/execution/alpaca.py ~800 lines after)
        ├── alpaca_models.py      # errors, constants, value objects
        ├── alpaca_client.py      # TradingClient adapter + normalization
        ├── risk_peak.py          # peak-equity sidecar (account-scoped, atomic, fail-closed)
        ├── alpaca_account.py     # account snapshot + ownership classification
        ├── alpaca_orders.py      # order submission + state normalization + ambiguous handling
        ├── alpaca_reconciliation.py # 7-bucket reconciliation coordinator
        ├── alpaca_protection.py  # protective-stop lifecycle (under/over, bracket Held)
        └── alpaca_crypto.py      # crypto synthetic OCO, emergency exit, per-symbol locks
                │
                ▼
        Alpaca TradingClient (REST, 15s timeout, close idempotent)
                │
                ▼
        ExecutionLedger + PortfolioStateRepository (callbacks, not owned)
```

**Facade:** `AlpacaBroker` remains the sole public import (`from parayabuck.execution.alpaca import AlpacaBroker, AmbiguousOrderState, ...`). Internally it composes the services above; callers never instantiate `AccountService` directly.

**Size goal:** `alpaca.py` < 1000 lines (facade + composition), no new module > 1200 lines without justification. Before: 2871 lines / 139856 bytes. After: facade ~700 + 7 modules averaging 300-500 lines.

## 2. Module Responsibilities

### 2.1 `alpaca_models.py`
- `CRYPTO_TAKER_FEE_RESERVE = Decimal("0.0025")` — single authoritative constant
- `RiskStatePersistenceError`, `AmbiguousOrderState`, `ProtectionReconciliationError`, `OwnershipDriftError`
- `CryptoPriceObservation`, `QuantityResolution`
- `OwnershipView` (internal) if useful, `ReconciliationIssue` etc.
- No broker calls, no ledger, pure value objects.

### 2.2 `alpaca_client.py`
- Thin adapter around `TradingClient`: `get_account`, `get_all_positions`, `get_open_position`, `get_asset`, `get_orders`, `get_order_by_id`, `get_order_by_client_id`, `submit_order`, `cancel_order_by_id`, `_request(FILL)`
- Session timeout `patch_session_timeout(15.0)`, `close()` idempotent
- Normalization helpers: `_field`, `field_value`, `_normalized_field`, `_asset`, `_price_step`, `_round_to_step`, order status mapping to `OrderState`
- Keeps `Alpaca SDK` DTO quirks isolated; domain services never call `getattr(raw, ...)` directly.

### 2.3 `risk_peak.py`
- `PeakStateService` owns `peak_equity`, `epoch`, `account_id` binding, `sidecar` path resolution, `atomic_write`, `ledger._lock` for `_query_persisted_peak`
- Fail-closed: malformed JSON, non-finite, negative, missing key, invalid epoch, unreadable → `RiskStatePersistenceError`; missing file → bootstrap 0.0
- Account-scoped: never cross-pollute peaks, `RuntimeError` on identity change
- `reset_peak_equity` operator verb (epoch+1, atomic)

### 2.4 `alpaca_account.py`
- `AccountService`: `raw account + raw positions + ledger.managed_for_account + portfolio_owned_quantities` → `AccountSnapshot`
- Blocked checks (`account_blocked`, `trading_blocked`, `trade_suspended_by_user`) → `RuntimeError`
- Market classification via `asset_class` or `is_crypto`
- Ownership: `managed` / `mixed` / `external`, `managed_qty = min(abs(raw), total_owned)`, `external_qty`, `qty_available`, dual lineage fail-closed, shortfall fee-reserve band + `_open_order_explains_drift` (SELL only, same symbol/market, remaining ≥ shortfall)
- Peak reconciliation via `risk_peak` before `max(peak, equity)`, cache `_snapshot_cache`

### 2.5 `alpaca_orders.py`
- `OrderGateway`: `PortfolioOrderRequest` → validated broker request → `submit` → `broker_order_id` or `AmbiguousOrderState`
- Quantity flooring to `quantity_increment`, notional check, open-order guard (`_has_open_order`), `MarketOrderRequest` (GTC crypto else DAY), `client_order_id` as recovery key
- `_submit_with_recovery`: on submit failure, try `get_order_by_client_id` → terminal re-raise, working recover, 404 re-raise, lookup fail → `AmbiguousOrderState` (no blind resubmit)
- `order_state` normalization to `OrderState` vocabulary, `filled_qty`/`avg_price`/`fees` centralization, unknown status → `UNKNOWN`/ambiguous

### 2.6 `alpaca_reconciliation.py`
- `ReconciliationService`: orchestrates `account()` → `fill-activity fee backfill` (watermarked 24h, paginated, idempotent) → `managed = ledger.managed_for_account` → 7-bucket holdings (`MATCH` etc.) → `_reconcile_stale_portfolio_ownership` → `_reconcile_crypto_exit_state` → `_reconcile_stale_ledger_entries` → protective-stop (re)submission (batch open-orders once, fee-reserve tolerance, nested bracket)
- Returns `dict[str, list[str]]` 7 buckets, sorted, never silently overwrites
- Idempotent: second call with same broker truth yields same buckets, no duplicate fills/stops

### 2.7 `alpaca_protection.py`
- `ProtectionService`: `detect existing protection` (`_has_protective_order`, fee-reserve, nested bracket via `get_order_by_id(nested=True)`), `calculate required_qty` (remaining effective, fee-reserve 0.9975 when `qty_available==0`), `under/over` handling, `cancel+confirm` before replace, `submit remainder` only, `re-protect` after execution changes
- Equity: fractional `DAY` vs integral `GTC`, crypto: `StopLimitOrderRequest` with `crypto_stop_limit_offset_bps` steps

### 2.8 `alpaca_crypto.py`
- `CryptoService`: `process_crypto_price` (per-symbol `threading.Lock` non-blocking), `target`/`stop`/`emergency` (`stop*(1-75bps)`) / `intrabar both-hit` → fetch stop status, `claim_symbol_exit` idempotent, `_cancel_stop_then_exit` (bounded 5s poll, 0.25s interval, verify terminal, partial fill → cancel remainder, ambiguous → manual intervention), `reprotection` of remainder, `pending exit` restart recovery via `reconcile`
- Never leaves unprotected remainder, never duplicate exit.

## 3. Dependency Direction

```
alpaca_models (no deps, pure)
      ↑
alpaca_client (depends on alpaca_models, parayabuck.core.models, utils)
      ↑
risk_peak, alpaca_account, alpaca_orders, alpaca_protection, alpaca_crypto, alpaca_reconciliation
      ↑ each depends on alpaca_client + alpaca_models + ledger callbacks
      ↑
alpaca.py (facade, composes all, re-exports public symbols)
      ↑
runtime.py, portfolio/runtime_service.py, web/managers/engine.py, cli.py
```

- No circular imports: services never import `alpaca.py`; `alpaca.py` imports services.
- Tests import `AlpacaBroker` from `alpaca.py` (facade); new services are internal but importable for unit tests.
- `ExecutionLedger` is injected, not owned; each service receives `ledger` only if it needs it (account, reconciliation, crypto).

## 4. Concurrency

- `symbol_exit_locks: dict[str, threading.Lock]` + `_symbol_exit_locks_guard` → same-symbol crypto exit serialized, different symbols not blocked, no global broker lock
- `asset_cache` access is single-threaded via `alpaca_client` (no lock needed, cache is per-broker)
- `snapshot_cache` is immutable `AccountSnapshot` + monotonic timestamp, no lock
- Ledger `store.lock` is held only inside `risk_peak._query_persisted_peak` and `ledger` methods, never across broker REST calls → no deadlock with broker locks

## 5. Observability

- `_emit_alert` remains in `alpaca_models` or `alpaca_client` but never affects order flow; webhook failure increments counters, never raises
- Execution events include `account_id`, `decision_id`, `client_order_id`, `broker_order_id`, `symbol`, `market` — never credentials
- Journal failure isolation: `try/except` around `alerts.emit`

## 6. Failure Semantics (summary)

| Condition | Exception / State | Retry? | Entry Halt? | Manual? |
|---|---|---|---|---|
| Network timeout before broker accept, lookup fails | `AmbiguousOrderState` | No (reconcile) | Yes (market) | Reconcile |
| Broker shortfall without SELL exemption | `OwnershipDriftError` | No | Yes (global) | Yes |
| Dual lineage (ledger+repo) | `OwnershipDriftError` | No | Yes | Yes |
| Sidecar corrupt | `RiskStatePersistenceError` | No | Yes | Yes |
| Unknown order status | `UNKNOWN` → ambiguous | No | Yes | Yes |
| Cancel unconfirmed | `AmbiguousOrderState` | No | Yes | Yes |
| Overprotection | Cancel+reconfirm then replace | No | No | No |

Full table: `docs/alpaca_broker_truth_matrix.md` and `docs/failure_semantics.md`.

## 7. Testing Strategy

- `FakeTradingClient` (`src/parayabuck/execution/fake_client.py`) — deterministic, counts economic actions, scriptable faults
- Characterization tests (`tests/execution/test_alpaca_characterization.py` 17 tests) pin BEFORE contract
- Fault matrix tests (`tests/execution/test_alpaca_faults.py`) cover submit/query/cancel faults, idempotency, concurrency
- Chaos scenario (`tests/execution/test_alpaca_chaos.py`) end-to-end 13-step with no duplicate actions

## 8. Migration

- `alpaca.py` keeps `__all__ = ["AlpacaBroker", "CryptoPriceObservation", "RiskStatePersistenceError"]` plus compatibility re-exports for `AmbiguousOrderState`, `ProtectionReconciliationError`, `OwnershipDriftError`, `CRYPTO_TAKER_FEE_RESERVE`, `QuantityResolution`
- Existing imports `from parayabuck.execution.alpaca import AlpacaBroker` continue to work; new code may import from `alpaca_models` or `fake_client` directly
- No behavior change: all 1093+ tests pass, ruff/mypy clean, backtest 3y ~1.29s preserved

## 9. Future

- `execution/ledger.py` remains the next dominant coupling (if further decomposition needed, it is Mission 8 candidate)
- `risk_peak` could be merged into `ledger` later, but kept separate now for account-scoping clarity
- No new background threads, no global broker lock, no framework.

---

*This architecture makes every economic action classifiable as proven, recovered, or explicitly ambiguous without reading a 140KB file.*
