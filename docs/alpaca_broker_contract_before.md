# AlpacaBroker Contract — BEFORE (read-only characterization)

> **Source:** `src/parayabuck/execution/alpaca.py` — 2872 lines, 139856 bytes, sha256:10fe9de76835ecbd
> **Generated:** 2026-08-29
> **HEAD:** a731517 (Mission 7 execution boundary)
> **Invariant:** NO code behavior changed — this document is a frozen read of the public surface before refactoring. Any drift from this file is a regression.

## 1. Purpose & Scope

`AlpacaBroker` is the **sole execution boundary** between ParayaBuck decision/runtime logic and Alpaca `TradingClient` (REST). It owns:

- account + position snapshot construction and ownership classification
- broker-authoritative trading increments (`asset_trading_rules`)
- portfolio order submission with deterministic idempotency recovery
- 7-bucket holdings reconciliation + fill-activity backfill + crypto protective-stop lifecycle
- synthetic crypto OCO via `process_crypto_price`
- account-scoped peak-equity / epoch persistence (fail-closed)

It MUST NOT change trading semantics (facade preserved), must fail-closed on ambiguity, and must never silently overwrite material differences between broker and local state.

---

## 2. Module Exports

|Symbol|Kind|Defined|Re-exported|
|---|---|---|---|
|`AlpacaBroker`|class|`alpaca.py:115`|`execution/__init__.py:1`, `__all__`|
|`CryptoPriceObservation`|`@dataclass(frozen=True)`|`alpaca.py:99-107`|`__all__`|
|`RiskStatePersistenceError`|`class(RuntimeError)`|`alpaca.py:57-64`|`__all__`|
|`AmbiguousOrderState`|`class(RuntimeError)`|`alpaca.py:85-86`|NOT in `__all__` but imported by runtime/tests|
|`ProtectionReconciliationError`|`class(RuntimeError)`|`alpaca.py:89-90`|NOT in `__all__`|
|`OwnershipDriftError`|`class(ProtectionReconciliationError)`|`alpaca.py:93-96`|NOT in `__all__`|
|`CRYPTO_TAKER_FEE_RESERVE`|`Decimal("0.0025")`|`alpaca.py:54`|imported by `portfolio/runtime_repository.py:19`|
|`QuantityResolution`|`@dataclass(frozen=True)`|`alpaca.py:110-113`|internal helper, public for typed callers|

---

## 3. Public Surface — Properties

|Property|Type|Line|Semantics|Set By|
|---|---|---|---|---|
|`account_id`|`str` (read-only)|`alpaca.py:249-251`|Alpaca account identity (`account.id` or `account_number`). Empty until first successful `account()`. Account-scoped peak reconciliation binds to this value. Changing identity after bind raises `RuntimeError`.|`account()`|
|`account_suffix`|`str` (read-only)|`alpaca.py:235-237`|Last 4 chars of `account_id` or `""`. Used for LIVE arming phrase `LIVE <suffix>` (`cli.py:332`, `web/managers/engine.py:776`). No broker call.|derived from `_account_id`|
|`manage_external_positions`|`bool`|`alpaca.py:126` attr|When `False` (default) external/mixed positions are left untouched by `reconcile()`. Legacy flag.|`__init__`|
|`ledger`|`ExecutionLedger|None`|`alpaca.py:129` attr|Durable ownership & fee ledger. Public but treated as injected collaborator.|
|`portfolio_owned_quantities`|`Callable[[], dict[str,float]]|None`|`alpaca.py:130`|Callback → repo owned quantities. Set by `runtime.py:276`.|
|`portfolio_open_orders`|`Callable[[], object]|None`|`alpaca.py:131`|Callback → repo open orders. Used for drift exemption & reconciliation.|
|`portfolio_fill_recovered`|`Callable[[str,str,float,float,datetime,str,float],None]|None`|`alpaca.py:132`|Callback invoked on recovered fills (stale ownership, fill-activity, crash resolution).|
|`portfolio_stop_recorded`|`Callable[[str,Market,float,str,float],None]|None`|`alpaca.py:133`|Callback invoked when portfolio crypto stop accepted; records protective order in repo.|
|`portfolio_flat_reconciled`|`Callable[[str,Market], object]|None`|`alpaca.py:134`|Callback for stale portfolio symbols proven flat.|
|`crypto_stop_limit_offset_bps`|`float` default 25.0|`alpaca.py:135`|Stop-limit offset in bps (`_submit_stop`).|
|`crypto_emergency_exit_bps`|`float` default 75.0|`alpaca.py:136`|Emergency failover offset below stop.|
|`crypto_stop_cancel_timeout_seconds`|`float` default 5.0|`alpaca.py:137`|Bounded poll for stop cancel.|
|`crypto_stop_cancel_poll_interval_seconds`|`float` default 0.25|`alpaca.py:138`|Poll interval.|

---

## 4. Public Surface — Methods

### 4.1 `__init__`

```python
def __init__(
    self,
    api_key: str,
    secret_key: str,
    paper: bool = True,
    *,
    manage_external_positions: bool = False,
    ledger: ExecutionLedger | None = None,
    portfolio_owned_quantities: Callable[[], dict[str,float]] | None = None,
    portfolio_open_orders: Callable[[], object] | None = None,
    portfolio_fill_recovered: Callable[[str,str,float,float,datetime,str,float],None] | None = None,
    portfolio_stop_recorded: Callable[[str,Market,float,str,float],None] | None = None,
    portfolio_flat_reconciled: Callable[[str,Market], object] | None = None,
    crypto_stop_limit_offset_bps: float = 25.0,
    crypto_emergency_exit_bps: float = 75.0,
    crypto_stop_cancel_timeout_seconds: float = 5.0,
    crypto_stop_cancel_poll_interval_seconds: float = 0.25,
    runtime_state_dir: str | Path | None = None,
    peak_state_path: str | Path | None = None,
) -> None
```

- **Location:** `alpaca.py:118-232`
- **Side effects:** constructs `TradingClient(api_key, secret_key, paper)`, patches session timeout 15s (`utils/http.py`), resolves `_peak_state_path` (priority: `peak_state_path` > `runtime_state_dir` > `data/runtime/paper|live_equity_peak.json`), bootstraps sidecar **eagerly only when `ledger is None`** (TOCTOU-safe direct read, fail-closed on corrupt file → `RiskStatePersistenceError`). With ledger, defers to first `account()` (`_ensure_peak_reconciled`).
- **Idempotency:** constructor is not retried; sidecar write is atomic (`utils/io.atomic_write`).
- **Called by:** `runtime.py:270`, `cli.py:329`, `cli.py:613`, `web/managers/engine.py:667`, `web/server_legacy.py:820`, `scripts/paper_smoke.py:355`, tests via `AlpacaBroker.__new__` bypass.

### 4.2 `close() -> None`

- **Line:** `alpaca.py:239-247`
- **Signature:** `def close(self) -> None`
- **Behavior:** closes `self._client._session` if present; swallows exceptions with warning; no-op for fake clients. **No broker call beyond session close.**
- **Called by:** `runtime.py:715/716`, `cli.py:335-336`, `web/managers/engine.py:562`, `scripts/paper_smoke.py:483`, `web/server_legacy.py:944`.
- **Idempotency:** safe to call multiple times (re-entrant guard `getattr(_session, None)`).

### 4.3 `snapshot_if_fresh(max_age_seconds: float) -> AccountSnapshot | None`

- **Line:** `alpaca.py:253-271`
- **Signature:** `def snapshot_if_fresh(self, max_age_seconds: float) -> AccountSnapshot | None`
- **Returns:** last `account()` snapshot if `time.monotonic() - _snapshot_cached_at <= max_age_seconds` else `None`. Immutable snapshot (broker-authoritative at fetch time).
- **Safety:** callers requiring post-write state MUST call `account()` instead. Only for config-sanctioned bounded staleness (entry-gate freshness, trade-update risk-state persistence that the 15s account loop re-verifies). Seed: `account()` sets `_snapshot_cache` + `_snapshot_cached_at`.
- **Called by:** `runtime.py:2073` (trade-update path: `if cached and age<=freshness -> reuse else fresh account()`). **NOT** used by portfolio runtime.
- **Idempotency:** pure read, no side effects.

### 4.4 `account() -> AccountSnapshot`

- **Line:** `alpaca.py:273-395`
- **Signature:** `def account(self) -> AccountSnapshot` (raises on blocked / drift)
- **Broker calls:** `self._client.get_account()`, `self._client.get_all_positions()`
- **Returns:** `AccountSnapshot(cash, equity, peak_equity, daily_pnl, positions, realized_pnl, unrealized_pnl, crypto_buying_power, crypto_trading_enabled, fees)`
- **Positions construction:** for each raw position: derives `market` via `asset_class` or `is_crypto`, `canonical_symbol`, classifies ownership `managed|mixed|external` via `ledger.managed_for_account` + `portfolio_owned_quantities` (fail-closed dual-ownership check), computes `managed_qty = min(abs(raw_qty), total_owned)`, `external_qty`, `qty_available`, binds `stop_price = managed.current_stop or original_stop`.
- **Ownership drift (fail-closed):** raises `OwnershipDriftError` if `ledger_owned>1e-12 and repo_owned>1e-12` (dual lineage) OR if (managed is None and `abs(raw_qty)+tol < total_owned*fee_reserve`) and no working managed SELL with sufficient remainder explains shortfall (`_open_order_explains_drift`, fee-adjusted). Exemption requires SELL, same symbol, remaining qty ≥ shortfall.
- **Peak reconciliation:** calls `_ensure_peak_reconciled(account_id, equity)` before `max(peak,equity)`, binds `_peak_account_id`, rewrites sidecar atomically if needed, enforces identity-change fail-closed (existing != new → `RuntimeError`).
- **Cache:** seeds `_snapshot_cache` / `_snapshot_cached_at = time.monotonic()`.
- **Raises:** `RuntimeError("Alpaca account is blocked...")` if `account_blocked|trading_blocked|trade_suspended_by_user`; `OwnershipDriftError`; `RiskStatePersistenceError`; `RuntimeError(account identity changed)`.
- **Called by:** `runtime.py:953,963,1237,1456,1703,1754` (account loop, universe pinning, risk persist, health), `portfolio/runtime_service.py:373,574,994`, `cli.py:331`, `web/managers/engine.py:676`, `scripts/paper_smoke.py:369`, `tests/test_portfolio_runtime.py:402` etc. (~35 call sites total).
- **Idempotency:** read-only REST; multiple calls return fresh snapshot (no dedup). Side effect is idempotent peak update + cache refresh.

### 4.5 `asset_trading_rules(symbol: str, market: Market) -> AssetTradingRules`

- **Line:** `alpaca.py:397-416`
- **Signature:** `def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules`
- **Broker call:** `self._asset(symbol)` → `self._client.get_asset(asset_lookup_symbol)` (cached in `_asset_cache` by canonical/lookup key).
- **Returns:** `AssetTradingRules(canonical_symbol, market, minimum, quantity_increment, price_increment, minimum_notional)` where defaults: `minimum`=1e-9, `quantity_increment`=1e-9, `price_increment`=0.01 equity else 1e-6 crypto, `minimum_notional`=1.0 equity else 10.0.
- **Raises:** `ValueError("Asset is not tradable")` if `not tradable`.
- **Called by:** `portfolio/runtime_service.py:702,1017,1130,1138` (planner, BUY re-plan, sizing), `alpaca.py:submit_portfolio_order` (validates qty/notional), `scripts/paper_smoke.py:375`.
- **Idempotency:** cached after first fetch; read-only.

### 4.6 `all_open_order_symbols() -> set[str]`

- **Line:** `alpaca.py:419-430`
- **Signature:** `def all_open_order_symbols(self) -> set[str]`
- **Broker call:** `self._client.get_orders(GetOrdersRequest(status=OPEN, nested=True))`
- **Returns:** set of canonical symbols for ALL working orders (managed + manual). Used as submission guard — bot must not stack orders.
- **Note:** deliberately broker-wide, NOT portfolio view.
- **Called by:** `portfolio/runtime_service.py:716,1025` (planner `open_order_symbols`), alternatives use direct API; tests mock via `SimpleNamespace`.
- **Idempotency:** read-only.

### 4.7 `portfolio_open_order_symbols() -> set[str]`

- **Line:** `alpaca.py:573-582`
- **Signature:** `def portfolio_open_order_symbols(self) -> set[str]`
- **No broker call.** Reads `self.portfolio_open_orders()` (repo) and returns canonical symbols of non-terminal portfolio orders. Manual broker orders never appear here — prevents pinning universe slots or suppressing drift.
- **Called by:** `runtime.py:1250,1463` (universe pinning, managed-symbol retention), `runtime.py:1463` union with `owned_symbols`.
- **Idempotency:** pure read of repo callback.

### 4.8 `resolve_portfolio_submission(client_order_id: str, broker_order_id: str = "") -> tuple[str,str] | str | None`

- **Line:** `alpaca.py:509-547`
- **Signature:** `def resolve_portfolio_submission(self, client_order_id: str, broker_order_id: str = "") -> tuple[str,str] | str | None`
- **Broker calls:** `get_order_by_client_id` → `get_order_by_id` fallback (404 = unknown). Network failures propagate.
- **Returns:**
  - `"canceled"` if unknown at broker (404) — order can never fill
  - `"filled"` (and invokes `portfolio_fill_recovered`) if status==filled and fill extractable
  - terminal status string (`canceled|cancelled|expired|rejected|done_for_day`) if present
  - `"submitted"` if still working
  - `None` if broker unreachable or lookup capability missing (`get_order_by_client_id is None`) — caller retries later without guessing
- **Raises:** never; `None` signals retry.
- **Called by:** `portfolio/runtime_service.py:932` (`_resolve_stuck_submission_rows`), indirectly `runtime.py` via repo recovery; tests in `test_portfolio_runtime.py:1140` etc.
- **Idempotency:** deterministic client-order-id lookup; recovery via stable activity id is idempotent.

### 4.9 `submit_portfolio_order(order: PortfolioOrderRequest) -> str`

- **Line:** `alpaca.py:631-692`
- **Signature:** `def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str` returns `broker_order_id`
- **Validations (fail-fast, emit alert `order_rejected`):**
  - quantity floored to `rules.quantity_increment` via `floor_increment_decimal`; raises `ValueError(below minimum size)` if < `minimum_order_size`
  - raises `ValueError(below minimum notional)` if `quantity*reference_price < minimum_notional`
  - raises `ValueError(Open order already exists)` if `_has_open_order(symbol)` true (symbol-specific `get_orders(OPEN, symbols=[symbol])`)
- **Broker call:** `MarketOrderRequest(symbol, qty, BUY|SELL, GTC for crypto else DAY, client_order_id) -> _submit_with_recovery -> TradingClient.submit_order`
- **Ambiguity recovery:** `_submit_with_recovery` on submit failure: if structured error contains `available`>0, raises original error (definite rejection, allows caller to size down). Otherwise tries `get_order_by_client_id`: terminal → re-raise original; working → returns recovered order; 404 → re-raise original (definite not-accepted); lookup failure → raise `AmbiguousOrderState` + emit `ambiguous_order`.
- **Returns:** `broker_order_id = str(result.id)`; raises `AmbiguousOrderState` if no id in response.
- **Called by:** `portfolio/runtime_service.py:1174` (`_submit_orders` + deferred BUYs), `scripts/paper_smoke.py:213`, tests.
- **Idempotency:** deterministic `client_order_id = parayabuck-{decision_id}-{symbol}-{side}` (`runtime_models.py:_decision_id` → sha256). Broker dedup via client-order-id + `UNIQUE(idx_orders_broker_id)`. Must not resubmit same id blindly; resolution via `resolve_portfolio_submission`.

### 4.10 `reconcile() -> dict[str, list[str]]`

- **Line:** `alpaca.py:694-1031`
- **Signature:** `def reconcile(self) -> dict[str, list[str]]`
- **Returns:** 7-bucket categorized report (never silently overwrites): `MATCH, LOCAL_STALE, BROKER_STALE, QUANTITY_MISMATCH, UNKNOWN_ORDER, MISSING_LOCAL_ORDER, MISSING_BROKER_ORDER` (sorted lists). Non-empty mismatch buckets must be treated fail-closed; caller halts entries until verified.
- **Execution order (load-bearing):**
  1. `account()` → fresh snapshot (accounts + positions) + peak bind
  2. fill-activity fee backfill + offline-fill recovery (`_reconcile_fill_activity_fees` → paginated `GET /account/activities/FILL`, watermarked 24h look-back, idempotent ON CONFLICT, sell activities → `ExecutionEvent(MANUAL_EXIT)` or fee leg only if already booked, buy activities → entry fill recovery)
  3. refresh `managed = ledger.managed_for_account` (fill recovery may have created lots)
  4. 7-category holdings reconciliation (ledger+portfolio vs broker quantities, tolerance `max(1e-9, qty*1e-8)`)
  5. stale portfolio ownership (`_reconcile_stale_portfolio_ownership`: fetch broker order by client/broker id; filled → recover via `portfolio_fill_recovered` true; working → true keep ownership; otherwise → `portfolio_flat_reconciled` adjustment)
  6. crypto exit state (`_reconcile_crypto_exit_state` → drives pending `exit_client_order_id` via `_reconcile_crypto_exit` or stop-failover `_cancel_stop_then_exit`)
  7. stale ledger entries (`_reconcile_stale_ledger_entries`: flat proven by snapshot → `mark_position_closed`; equity defers if activities not reconciled)
  8. protective-stop (re)submission per position (batch open-orders fetch once, `_has_protective_order` with fee-reserve 0.25% tolerance when `qty_available==0`, nested bracket detection, oversize → cancel+reconfirm before replace, undersize → add remainder, crypto uses `_submit_stop` with portfolio-owned emergency-exit stop `crypto_emergency_exit_bps`)
- **Raises:** `ProtectionReconciliationError` (broker qty below ledger, no qty to protect, could not restore), `OwnershipDriftError`, `AmbiguousOrderState` (cancel unconfirmed). `runtime.py:760` maps these to `halted` + `_circuit_breaker_reason = protection_failure|reconciliation_failed`.
- **Called by:** `runtime.py:761` (startup, periodic 60s via `_schedule_reconcile`), `scripts/paper_smoke.py:329,450`, tests `test_hardening.py::test_external_position_is_not_modified_by_default`.
- **Idempotency:** reconciliation is intentionally re-entrant; categories are recomputed fresh each call; pending crypto exits claimed via idempotent ledger claim.

### 4.11 `resolve_available_position_qty(symbol: str, requested_qty: float, *, reserve_fee_on_lookup_failure: bool = False, attempts: int = 5) -> QuantityResolution`

- **Line:** `alpaca.py:1595-1670`
- **Signature:** `def resolve_available_position_qty(self, symbol: str, requested_qty: float, *, reserve_fee_on_lookup_failure: bool = False, attempts: int = 5) -> QuantityResolution`
- **Returns:** `QuantityResolution(verified: bool, available_qty: float, source: str)` with sources: `broker_position`, `broker_position_no_available_qty`, `broker_lookup_failed`, `verified_fill_fee_reserve`.
- **Logic:** loops up to `attempts` (0.2s sleep) fetching `get_open_position(asset_lookup_symbol)` (crypto uses no-slash form `BTCUSD`). Uses `qty_available` if present else `qty`. Returns floored `min(requested, available)`. If never saw position and `reserve_fee_on_lookup_failure` true → fee-reserve fallback `requested*(1-0.0025)` floored (permitted ONLY immediately after verified crypto entry fill while installing protection); otherwise fail-closed `verified=False, available=0`.
- **Called by:** internal `_available_position_qty` wrapper → used by `_reprotect_after_terminal_exit`, `_cancel_stop_then_exit`, `_flatten`; tests `test_hardening.py:196`.
- **Safety:** fail-closed default; verified=false paths halt crypto exits.

### 4.12 `process_crypto_price(observation: CryptoPriceObservation) -> str | None`

- **Line:** `alpaca.py:1912-2013`
- **Signature:** `def process_crypto_price(self, observation: CryptoPriceObservation) -> str | None`
- **Params:** `observation: CryptoPriceObservation(symbol, high, low, source, timestamp)` (frozen dataclass, `alpaca.py:99`).
- **Returns:** `"target"|"stop"|"stop_failover"|"intrabar_stop"|None`.
- **Behavior:** idempotent ledger claim (`ledger.claim_symbol_exit(account_id, entry_id, reason)`); per-symbol `threading.Lock` (non-blocking, defers if busy); checks `observation.high >= original_target` and `observation.low <= current_stop`; intrabar both-hit → resolves via broker stop order fetch (filled vs unfilled); target hit → claim + `_cancel_stop_then_exit` ; emergency failover when `low <= stop*(1 - emergency_bps/10000)`; stop triggered unfilled → marks `triggered_unfilled`.
- **Broker calls:** `get_order_by_id`, `cancel_order_by_id`, `submit_order(MarketOrderRequest GTC SELL)` via `_cancel_stop_then_exit`.
- **Raises:** `AmbiguousOrderState` if stop unknown or cancellation unconfirmed (marks manual intervention, halts crypto entries).
- **Called by:** `runtime.py:2239` (`_process_crypto_protection` drives via `asyncio.to_thread` per bar), tests `test_crypto_universe.py:310`.
- **Idempotency:** claim is idempotent — only winner proceeds; losers skip.

### 4.13 Additional Externally-Invoked Surface (observed via grep)

|Method|Where observed|Note|
|---|---|---|
|`all_open_order_symbols` (broker-wide)|`portfolio/runtime_service.py:716`|Planner guard; distinct from portfolio view|
|`portfolio_open_order_symbols`|`runtime.py:1250,1463`|Universe pinning|
|`resolve_portfolio_submission`|`portfolio/runtime_service.py:932`|Stuck `submitting` resolver|
|`reset_peak_equity(equity: float)`|`runtime.py:3659` (via web `start_new_risk_epoch` → `broker.reset_peak_equity`)|Operator-only rebasing; see §6.1|
|`_load_peak_equity / _save_peak_equity`|legacy tests `test_predeploy_hardening.py`, private but load tests call|Now account-scoped delegates to `_ensure_peak_reconciled`|
|`_position_qty`, `_has_open_order`, `_submit_stop`|`tests/test_hardening.py` and `scripts/paper_smoke.py:293` (private but monkeypatched by tests)|Not part of public contract; characterized for completeness|

---

## 5. Exceptions — Contract

|Exception|Bases|Line|When raised|Fail-closed action|
|---|---|---|---|---|
|`RiskStatePersistenceError`|`RuntimeError`|`alpaca.py:57`|Peak sidecar present but unreadable/invalid: missing `peak_equity` key, JSON malformed/truncated, non-finite, negative, unreadable, epoch not int or negative. Also if `portfolio_high_water` or `risk_state` SQLite reads fail or all rows corrupt. Missing file bootstraps to 0.0.|Halt entries: caller must not fallback to 0.0 and erase watermark. `runtime.py:1766` → `halted`.|
|`AmbiguousOrderState`|`RuntimeError`|`alpaca.py:85`|Broker write outcome unknown (submit error + lookup failed/unconfirmed), protective cancel unconfirmed, filled during cancel, stop cancellation unconfirmed, crypto exit not terminal, partial cancel unconfirmed, intrabar path unresolved, flatten position unverified. Emits `ambiguous_order` alert.|Halt market entries (`runtime.py:2258 ambiguous_crypto_exit`) or global halt (`reconcile → reconciliation_failed`). No blind retry; only `reconcile()` next cycle.|
|`ProtectionReconciliationError`|`RuntimeError`|`alpaca.py:89`|Could not restore managed protection, broker qty below ledger, no available qty to protect, cannot recover crypto exit (no ledger/getter), crypto exit remains non-terminal, remaining qty unverified.|Global halt `protection_failure` (`runtime.py:778`).|
|`OwnershipDriftError`|`ProtectionReconciliationError`|`alpaca.py:93`|Dual ownership lineage (ledger + repo both >1e-12) or repo-managed qty exceeds broker qty by >fee_reserve (and no working managed SELL explains shortfall). Includes `_open_order_explains_drift` fee-adjusted check.|Global halt `ownership_drift` survives restart (`safety_incidents`).|

- **Hierarchy:** `OwnershipDriftError ⊂ ProtectionReconciliationError ⊂ RuntimeError` so `_is_transient_network_error` (`runtime.py:109`) returns `False` for all three (never retried via `TransientStartupError`).
- **Transient vs permanent:** `_is_transient_network_error` treats only `RequestException, ConnectionError, TimeoutError, 5xx/429` as retryable. Domain safety errors above are permanent fail-closed.

---

## 6. Persistence & Peak Semantics

### 6.1 `reset_peak_equity(equity: float) -> None` (operator verb)

- **Location:** `alpaca.py:2838-2866`
- **Contract:** `equity` must be >0 else `ValueError`; `account_id` must be known (via `account()` or reconciled sidecar) else `RuntimeError`. New epoch = `max(sidecar_epoch, persisted_epoch)+1` for **this account only**. Sets `_peak_equity = equity`, `_peak_epoch+=1`, `_peak_account_id=account_id`, `_peak_reconciled=True`, atomically rewrites sidecar `{account_id, peak_equity, epoch}`.
- **Called by:** `runtime.py:3659` → `EngineRuntime.start_new_risk_epoch` → `web/server.py:dashboard_risk POST /api/safety/risk-epoch`.

### 6.2 Sidecar file

- **Path:** `data/runtime/<paper|live>_equity_peak.json` (or `runtime_state_dir` / `peak_state_path` override). JSON `{account_id: str, peak_equity: float, epoch: int}`. Legacy without `account_id` → tolerated then rebound on first `account()`.
- **Atomic write:** `utils/io.atomic_write` (temp + `os.replace`). Validated to survive injected failure (`test_predeploy_hardening.py:85`).
- **Account-scoped rules (`_ensure_peak_reconciled`):**
  - Case A both None → peak 0.0 epoch 0 (genuine first run) — do NOT create sidecar yet.
  - Sidecar only → use sidecar, warn if legacy, bind.
  - Persisted only → use persisted.
  - Both → legacy sidecar: `max(legacy, persisted)` then bind; bound sidecars: newer epoch wins, same epoch → max peak. Mismatched sidecar account → ignore sidecar + warn + rewrite bound.
  - Identity change (`existing != account_id`) → `RuntimeError` fail-closed.
  - SQLite read failures → `RiskStatePersistenceError`.
  - SQLite read failures → `RiskStatePersistenceError`.

### 6.3 Ledger peak sources

- `portfolio_high_water(account_id)` (equity, epoch) and `risk_state payload.peak_equity/peak_epoch` (per-day rows) queried in `_query_persisted_peak` (holds `ledger._lock`); handles missing tables. Max persisted = cross-source resolved per rules above.

---

## 7. Constants & Value Objects

|Name|Value|Location|Usage|
|---|---|---|---|
|`CRYPTO_TAKER_FEE_RESERVE`|`Decimal("0.0025")` (0.25%)|`alpaca.py:54`|Fee deduction from acquired crypto asset: reduces `required_qty` when `qty_available==0` (`account()`, `reconcile()`, `resolve_available_position_qty` fee-reserve fallback, `runtime_repository.py:836` fill delta).|
|`CryptoPriceObservation`|`(symbol:str, high:float, low:float, source:str, timestamp:datetime)`|`alpaca.py:99`|Bar OHLC passed to `process_crypto_price`; `symbol` re-canonicalized, `high` vs target, `low` vs stop/emergency.|
|`QuantityResolution`|`(verified:bool, available_qty:float, source:str)`|`alpaca.py:110`|Result of `resolve_available_position_qty`; source ∈ {broker_position, broker_position_no_available_qty, broker_lookup_failed, verified_fill_fee_reserve}.|
|`AssetTradingRules`|`(canonical_symbol, market, minimum, quantity_increment, price_increment, minimum_notional)`|`execution/portfolio.py`|From `asset_trading_rules`; used by planner + submission.|
|`AccountSnapshot`|`(cash, equity, peak_equity, daily_pnl, positions, realized_pnl, unrealized_pnl, crypto_buying_power, crypto_trading_enabled, fees, drawdown_pct)`|`core/models.py`|Returned by `account()` and `snapshot_if_fresh`.|
|`PositionSnapshot`|`(symbol, qty, avg_price, current_price, market, qty_available, ownership, managed_qty, stop_price, initial_risk, ...)`|`core/models.py`|Element of `AccountSnapshot.positions`; ownership ∈ {managed,mixed,external}.|

---

## 8. Idempotency & Safety Rules (MUST)

1. **Deterministic client_order_id:** `parayabuck-{decision_id}-{symbol}-{side}` where `decision_id = sha256(account_id|config_digest|timestamp|market)[:24]` (`runtime_models.py`) and buy leg appends via same. Broker dedup via client-order-id + `UNIQUE(idx_orders_broker_id)` enforce.
2. **Persist-before-broker:** `record_decision_with_orders(..., rebalance_phase)` transaction before any `submit_portfolio_order`. Crash between persist and ack resolved via `resolve_portfolio_submission`: 404 → `canceled` (can never fill), filled → recover via `portfolio_fill_recovered`, working → `submitted`, unreachable → `None` retry later (no guessing).
3. **No silent overwrite:** `reconcile()` never silently reconciles holdings; 7 buckets explicitly categorized, caller must halt on non-empty mismatch.
4. **Fail-closed drift:** dual lineage or repo>broker (fee-adjusted) → `OwnershipDriftError` even if broker appears flat after manual sell; only working managed SELL with sufficient remaining qty exempts drift (`_open_order_explains_drift`). Cross-market symbol excluded.
5. **Fee-reserve band:** crypto `required_qty` reduced to 99.75% only when `qty_available==0` and not broker_qty_is_fee_adjusted; otherwise protect full qty. Fallback reduction via `resolve_available_position_qty(reserve_fee_on_lookup_failure=True)` permitted ONLY immediately after verified crypto entry fill while installing protection; exit paths default fail-closed.
6. **Protective-stop lifecycle:** fee-aware `_has_protective_order` (tolerance `max(1e-9, qty*1e-8)`), nested bracket detection via `get_order_by_id(nested=True)`, oversize fee-band (protected_qty ≤ total_qty+tol) treated healthy (no churn), otherwise cancel+confirm before replace, undersize → add remainder. Retry uses different client id; accepted id recorded via `portfolio_stop_recorded`.
7. **Crypto synthetic OCO:** broker stop always remains active until target/failover claim won + cancellation confirmed (bounded 5s + 0.25s poll). Intrabar both-hit → fetch broker stop status. Claim via `ledger.claim_symbol_exit` is idempotent; lock per symbol non-blocking defers observation.
8. **Peak is monotonic per-account:** only `reset_peak_equity` may rebase; file corrupt → `RiskStatePersistenceError` never fallback to 0. Sidecar rewrite atomic; identity change → `RuntimeError`.
9. **Session handling:** `TradingClient._session` patched timeout 15s; `close()` drains pool (no-op for fakes). Never call `close_position` (would consume manual inventory) — use `MarketOrderRequest` with verified qty.
10. **No code change:** this doc freezes behavior; any new method must preserve facade, remain fail-closed, and keep idempotency keys stable.

---

## 9. Dependency Map (before)

```
TradingClient (alpaca.trading.client)
   ↑
AlpacaBroker ──→ ExecutionLedger (orders/fills/managed_positions/risk_state/reconcile_watermarks)
   │                ↑ (via ledger._lock + connection)
   ├─→ PortfolioStateRepository callbacks (owned_quantities, open_orders, fill_recovered, stop_recorded, flat_reconciled)
   ├─→ Asset cache (get_asset)
   ├─→ atomic_write (peak sidecar) + patch_session_timeout
   └─→ TradingClient REST: get_account, get_all_positions, get_open_position, get_asset, get_orders(GetOrdersRequest), get_order_by_id(GetOrderByIdRequest), get_order_by_client_id, submit_order(MarketOrderRequest|StopOrderRequest|StopLimitOrderRequest), cancel_order_by_id, _request(GET /account/activities/FILL)
```

- **Inbound (callers):** `EngineRuntime` (runtime.py), `PortfolioRuntime` (portfolio/runtime_service.py), `EngineManager` (web/managers/engine.py + web/server_legacy.py), `cli.py`, `scripts/paper_smoke.py`, `AlpacaHistoricalData` is independent.
- **No inbound:** ledger does NOT call broker; broker calls ledger.

---

## 10. Non-Public / Legacy (characterized, not part of contract)

- Private helpers: `_asset, _price_step, _round_to_step, _wait_for_fill, _available_position_qty, _available_qty_from_error, _flatten, _position_qty, _has_open_order, _has_protective_order, _open_protective_orders, _cancel_protective_orders, _wait_for_cancel_confirmation, _protection_matches, _normalized_field, _field, _query_persisted_peak, _ensure_peak_reconciled, _load_peak_equity, _save_peak_equity, _round_qty_down, _qty_step, _submit_stop, _has_nested_bracket_protection, _reconcile_stale_portfolio_ownership, _fetch_broker_order, _fill_from_broker_order, _portfolio_open_order_map, _open_order_explains_drift, _reconcile_crypto_exit_state, _reconcile_stale_ledger_entries, _reconcile_fill_activity_fees, _reconcile_crypto_exit, _reprotect_after_terminal_exit` — stable internals; tests monkeypatch some via `__new__` but NOT semver.
- Legacy submission surface (`submit, replace_protective_stop, protect_recovered_crypto_entry`) deleted per `product_audit.md G6` — not present in this file.
- `ExecutionBroker` protocol removed; `PortfolioRuntimeBroker` protocol now minimal (`account, asset_trading_rules, all_open_order_symbols, submit_portfolio_order`) — broker implements superset.

---

## 11. Verification Checklist

- [ ] `rg -n "class AlpacaBroker" src/parayabuck/execution/alpaca.py` shows line 115
- [ ] `rg -n "def (account|close|snapshot_if_fresh|asset_trading_rules|submit_portfolio|reconcile|resolve_portfolio|portfolio_open|process_crypto|resolve_available|reset_peak)" src/parayabuck/execution/alpaca.py` matches §4 list
- [ ] `rg -n "CRYPTO_TAKER_FEE_RESERVE|CryptoPriceObservation|AmbiguousOrderState|ProtectionReconciliationError|OwnershipDriftError|RiskStatePersistenceError" src/parayabuck/execution/alpaca.py` matches §2/5/7
- [ ] `wc -l src/parayabuck/execution/alpaca.py` ≈ 2872, `wc -c` ≈ 139856, sha256 prefix `10fe9de76835ecbd` quoted above
- [ ] No diff in `git diff -- src/parayabuck/execution/alpaca.py` after doc creation (no code changed)

---

*End — this is the frozen BEFORE contract for Mission 7. Any refactoring MUST preserve every signature, exception, and safety rule above.*

