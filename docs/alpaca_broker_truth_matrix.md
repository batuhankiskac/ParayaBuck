# AlpacaBroker Truth Matrix — BEFORE

> **Source:** `src/parayabuck/execution/alpaca.py` @ `a731517` (2872 lines, sha 10fe9de7)
> **Purpose:** For every broker observation, what fact is proven, what action is allowed, and what is FORBIDDEN. Fail-closed. No guessing.

## How to read

|Broker observation|Proven fact (at fetch time)|Allowed action|Forbidden action|Code anchor|
|---|---|---|---|---|
| **This table is the contract.** Every `reconcile()` / `account()` / `submit` path must map to one row before acting.

---

## 1. Account / Position Snapshot (`account()`)

|#|Broker observation|Proven fact|Allowed action|Forbidden|
|---|---|---|---|---|
|A1|`account.account_blocked` or `trading_blocked` or `trade_suspended_by_user` is true|Account is not tradable now|Raise `RuntimeError("Alpaca account is blocked from trading")`, halt entries|Proceed to submit or treat as transient retry|
|A2|`ledger_owned>1e-12 && repo_owned>1e-12` for same canonical symbol|Dual ownership lineage — two systems claim same shares|Raise `OwnershipDriftError(dual lineage)`, halt entries, require operator reconcile|Pick one quantity, silently sum, or auto-merge|
|A3|`abs(broker_qty)+tol < total_owned*0.9975` (crypto) or `< total_owned` (equity), **and** no working managed SELL with remaining ≥ shortfall|Broker holds fewer shares than repo claims as managed (drift)|Raise `OwnershipDriftError(ownership drift)`, halt|Fall back to broker qty, overwrite repo, or assume sell filled|
|A4|Same shortfall as A3 **but** a working managed SELL (portfolio repo, same symbol/market, remaining qty ≥ shortfall) exists|Shortfall is attributable to an inflight closing order|Suppress drift for that symbol (return snapshot)|Suppress drift for BUY, manual order, or undersized SELL; suppress drift for cross-market symbol|
|A5|`abs(broker_qty)` within fee band of `owned_qty` (crypto, `qty_available==0` tolerance)|Fee-adjusted position (Alpaca deducted taker fee)|Accept as healthy, compute `managed_qty = min(abs(raw), owned)`, ownership = managed/mixed|Treat fee band as over-protection churn (do not re-submit stop)|
|A6|`managed_qty>0` and `external_qty<=1e-12`|Position is fully managed|`ownership="managed"`|Treat as external|
|A7|`managed_qty>0` and `external_qty>1e-12`|Mixed inventory (bot + manual)|`ownership="mixed"`, `managed_qty` = bot portion, `external_qty` = manual portion; stops protect only managed_qty|Call `close_position` (would consume manual units)|
|A8|`managed_qty==0` but `raw_qty>0`|External-only position|`ownership="external"`, `managed_qty=0`|Protect it unless `manage_external_positions=True`|
|A9|`get_account` returns equity, `get_all_positions` returns positions|Fresh broker truth at fetch time|Seed `_snapshot_cache`, bind `_peak_account_id`, `max(peak,equity)` then `atomic_write` if needed|Reuse stale snapshot for post-write drift check|
|A10|Sidecar JSON present but missing `peak_equity` / malformed / truncated / non-finite / negative / unreadable, or epoch invalid|Persistence is corrupt, watermark would be erased|Raise `RiskStatePersistenceError`, halt entries fail-closed|Fall back to 0.0|
|A11|Sidecar `account_id` mismatches current `account_id` (or fingerprint mismatch)|Sidecar belongs to different account|Ignore sidecar, warn, use persisted for current account, rewrite bound sidecar|Cross-account peak pollute|
|A12|`existing _peak_account_id != new account_id` (identity change mid-process)|Account switched without restart|Raise `RuntimeError(account identity changed)`, halt|Continue with stale peak|

---

## 2. Holdings Reconciliation — 7 Buckets (`reconcile()` categories)

Derived from `ledger + portfolio` vs `broker` quantities with tolerance `max(1e-9, qty*1e-8)`:

|Bucket|Proven fact|Allowed|Forbidden|
|---|---|---|---|
|`MATCH`|Local (ledger+portfolio) qty ≈ broker qty (within tol)|Record `MATCH` for symbol|Ignore mismatch|
|`LOCAL_STALE`|Local >0, broker 0 (broker flat)|Also append to `MISSING_BROKER_ORDER`; stale portfolio entry → try `_reconcile_stale_portfolio_ownership` before `flat_reconciled` adjustment|Silently clear ownership without broker order lookup|
|`BROKER_STALE`|Local 0, broker >0|Append to `MISSING_LOCAL_ORDER`; warn; protect if `manage_external` or crypto portfolio-owned|Auto-adopt as managed|
|`QUANTITY_MISMATCH`|Both >0 but `abs(local-broker)>tol`|Append `QUANTITY_MISMATCH`|Overwrite local with broker|
|`UNKNOWN_ORDER`|`broker_open_ids - local_open_ids` (broker has order local doesn't)|Append `UNKNOWN_ORDER` per client_order_id|Assume it's managed|
|`MISSING_BROKER_ORDER`|`local_open_ids - broker_open_ids` (local expects order missing at broker)|Append `MISSING_BROKER_ORDER` + `MISSING_LOCAL_ORDER` (alias for stale)|Ignore|
|`MISSING_LOCAL_ORDER`|Broker has open order not in local (same as UNKNOWN but per-symbol bucket)|Listed above|—|

Empty mismatch buckets (`QUANTITY_MISMATCH`, `LOCAL_STALE`, `BROKER_STALE`, `UNKNOWN_ORDER` non-empty) must be treated fail-closed by caller (`runtime.py` halts).

---

## 3. Portfolio Order Submission (`submit_portfolio_order`)

|#|Broker observation|Proven fact|Allowed|Forbidden|
|---|---|---|---|---|
|S1|`quantity < minimum_order_size` (after floor to `quantity_increment`)|Order is definitively below minimum|Emit `order_rejected(below_minimum_size)`, raise `ValueError`|Submit anyway|
|S2|`quantity*reference_price < minimum_notional`|Order is definitively below notional|Emit `order_rejected(below_minimum_notional)`, raise `ValueError`|Submit anyway|
|S3|`_has_open_order(symbol)` true (any working order for symbol)|Symbol already has working order|Emit `order_rejected(open_order_exists)`, raise `ValueError`|Stack second order|
|S4|`submit_order` raises with JSON `{"available": Y}` where `0<Y<requested`|Broker confirms available < requested (structured rejection)|Raise original error (allows caller to size down) — **not ambiguous**|Treat as ambiguous or retry same qty|
|S5|`submit_order` raises generic error, then `get_order_by_client_id` returns terminal (`rejected|canceled|expired`)|Broker confirms rejection|Re-raise original submit error (definite)|Treat as ambiguous|
|S6|Same, but lookup returns working order|Broker accepted despite submit error|Return recovered order (log warning)|Raise ambiguous|
|S7|Same, but lookup 404|Client id never accepted|Re-raise original submit error (definite not-accepted)|Treat as ambiguous|
|S8|Same, but lookup fails (network)|Outcome unknown, lookup itself failed|Log warning, raise `AmbiguousOrderState(broker write outcome unknown)` + alert `ambiguous_order`|Assume canceled or assume filled|
|S9|`submit_order` succeeds but `result.id` empty|Response has no broker id|Emit `ambiguous_order`, raise `AmbiguousOrderState`|Return empty id|

---

## 4. Stuck Submission Resolution (`resolve_portfolio_submission`)

|#|Broker observation via `get_order_by_client_id` (fallback `get_order_by_id`)|Proven fact|Allowed action|Forbidden|
|---|---|---|---|---|
|R1|404 / `None`|Order never reached Alpaca, can never fill|Return `"canceled"` (safe to mark canceled)|Mark filled or leave submitting forever|
|R2|Status `filled` and `filled_qty>0 && avg_price>0`|Order filled at broker at real price|Invoke `portfolio_fill_recovered` (recovery path), return `"filled"`|Invent synthetic fill at reference price|
|R3|Same but filled but `filled_qty/avg_price` unextractable|Fill not provable|Return `"submitted"` (keep non-terminal, don't release ownership)|Guess fill qty/price|
|R4|Status `canceled|cancelled|expired|rejected|done_for_day`|Terminal; any `filled_qty>0 && avg_price>0` is a real partial fill|Invoke `portfolio_fill_recovered(..., status=<status>)` for a real partial fill (cumulative, so a later delivery never double-books), then return that status|Mark filled; drop a real partial fill|
|R5|Status `accepted|pending|working|partially_filled` etc.|Still working|Return `"submitted"`|Mark completed|
|R6|`get_order_by_client_id` missing / `None` capability, or network error|Broker unreachable|Return `None` (caller retries later, no state change)|Guess|
|R7|Exception with `status_code != 404` during fetch|Lookup itself failed|Propagate (do not guess) → caller treats as `None` retry|Swallow as 404|

---

## 5. Available Quantity Resolution (`resolve_available_position_qty`)

|#|Broker observation|Proven fact|Allowed|Forbidden|
|---|---|---|---|---|
|Q1|`get_open_position` returns `qty_available` (or `qty`) and `saw_position=True`, `available = min(requested, abs(avail))` floored >0|Broker verifies sellable qty|Return `QuantityResolution(True, floored, "broker_position")`|Use unverified qty|
|Q2|Same but floored qty ==0|Nothing sellable right now, but position exists|Return `QuantityResolution(True, 0, "broker_position_no_available_qty")`|Return fee-reserve|
|Q3|`get_open_position` raises `AttributeError` (shape change)|Payload unreadable|Log warning, break, return fail-closed (no fee reserve unless allowed)|Retry fee-reserve|
|Q4|Other exception, retried up to 5× with 0.2s sleep, never saw position|Broker lookup failed, no verification|If `reserve_fee_on_lookup_failure=False` (default exit path) → `QuantityResolution(False, 0, "broker_lookup_failed")`|Assume full qty|
|Q5|Same but `reserve_fee_on_lookup_failure=True` (only allowed immediately after verified crypto entry fill while installing protection)|Verified fill exists, fee band applies|Return `QuantityResolution(False, floor(requested*0.9975), "verified_fill_fee_reserve")`|Use this fallback on exit paths|
|Q6|Crypto `BTC/USD` normalizes to `BTCUSD` for lookup; equity `IWM` stays `IWM`|Symbol mapping proven|Use `normalize_symbol(...).asset_lookup_symbol`|Use canonical slash form for crypto lookup (404)|

Fail-closed default (`verified=False, 0`) halts crypto exits (`_cancel_stop_then_exit` raises `AmbiguousOrderState`).

---

## 6. Protective Stop Lifecycle (`_submit_stop` / `_has_protective_order` / `_open_protective_orders`)

|#|Broker observation|Proven fact|Allowed|Forbidden|
|---|---|---|---|---|
|P1|`tradable==false` in `get_asset`|Asset not tradable|`asset_trading_rules` raises `ValueError`|Submit|
|P2|Open orders contain closing-side (`sell` if long) stop/stop_limit/trailing_stop with `status not in {canceled,filled,rejected...}` and qty (remaining = qty - filled_qty) summed = `protected`|Protection exists (remaining only)|`_has_protective_order` returns match if `abs(protected-required) <= max(1e-9, required*1e-8)`|Double-count filled portion|
|P3|Same but `protected==0` → `mismatch=missing`; `protected < required-tol` → `under`; `protected > required+tol` → `over`|Undersized / oversized|Undersize → submit remainder only (`required-protected`); Oversize → cancel+confirm before replace|Blindly re-submit same size|
|P4|Fee band: `protected_qty ≤ total_qty + max(1e-9, total_qty*1e-8)` and `required` was `total*0.9975` because `qty_available==0`|Healthy full-size stop covering whole position (fee reserve)|Treat as protected, skip churn (set ACTIVE, continue)|Cancel and churn|
|P5|`open_orders` batch provided (broker-wide)|Shared snapshot|`_open_protective_orders` replicates server `symbols=[symbol]` filter client-side via `same_symbol`|Treat unknown-symbol order as protective for this position|
|P6|Ownership of order: `order_id/parent_id/client_id` in `ledger.managed_*` or `client_id.startswith("parayabuck-")` or `ledger is None && manage_external`|Order is owned (inherited via legs)|Consider it protective|Treat manual order as protective|
|P7|Broker-wide open orders contain only active TP leg, but `get_order_by_id(nested=True)` for parent shows HELD stop leg|Bracket protection still Held|`_has_nested_bracket_protection` treats as protected|Submit second stop while bracket reserves position|
|P8|`_submit_stop` with `qty<=0` or `stop_price<=0` after rounding to `price_increment` step|Would round to zero|Skip, log warning, return `None`|Submit zero|
|P9|Crypto stop: `limit_price = stop ± distance_steps*step` where `distance_steps = max(1, ceil(stop*offset/step))`, offset 25bps|Stop-limit with guarantee|Build `StopLimitOrderRequest(GTC)`|Use stop-market for crypto|
|P10|Equity stop: fractional qty → `quantize(1e-9, ROUND_DOWN)` + `DAY`; integral → `int` + `GTC`|Fractional equity requires DAY|Build `StopOrderRequest` with correct TIF|Use GTC for fractional|
|P11|Submit oversize crypto stop raises with `available: Y` where `0<Y<qty`|Broker reports available < requested|Round Y down to step, retry with new `parayabuck-stop-{uuid}` via `_submit_with_recovery`|Retry same client id|
|P12|Retry accepted with different client id|New id is authoritative|Return new client id; caller must record accepted id via `portfolio_stop_recorded`|Keep old id|

---

## 7. Crypto Synthetic OCO (`process_crypto_price`)

|#|Broker / Market observation|Proven fact|Allowed|Forbidden|
|---|---|---|---|---|
|C1|No ledger or no `_account_id`|No managed crypto state|Return `None` (no-op)|Proceed|
|C2|Per-symbol `threading.Lock` busy (non-blocking `acquire` fails)|Another thread is driving exit for this symbol|Log warning, return `None` deferred|Block or stack exits|
|C3|`observation.high >= original_target` AND `observation.low <= active_stop` (intrabar both hit)|Path ambiguous (which hit first)|Fetch `get_order_by_id(stop_broker_id)`; if `filled` → claim `intrabar_stop` + cancel-then-exit → return `"stop"`; else mark manual intervention `AmbiguousOrderState`|Assume target won|
|C4|Same but `protective_stop_broker_order_id` missing|Stop unknown|Mark manual intervention, raise `AmbiguousOrderState`|Guess|
|C5|Only `high >= target` (target hit)|Target triggered|Claim via `ledger.claim_symbol_exit(reason="target")`; if claim won → `_cancel_stop_then_exit("target")` → return `"target"` ; if claim lost → skip|Blindly submit without claim|
|C6|`low <= emergency_price` where `emergency = stop*(1 - 75bps)` (default 75)|Emergency failover triggered|Claim `stop_failover` → cancel-then-exit|Treat as normal stop trigger|
|C7|`low <= stop` and `stop_state=="open"` (stop touched but not failover)|Stop triggered but remains unfilled|Mark `stop_state=triggered_unfilled`, log warning, **do not exit yet** (broker stop still active)|Immediately exit without cancel|
|C8|`claim_symbol_exit` returns `None` (another bar/thread already claimed)|Exit already claimed|Return `None` (idempotent skip)|Double-exit|
|C9|Stop cancel: `cancel_order_by_id` raises but poll confirms `canceled|cancelled|expired`|Cancel succeeded despite error|Proceed to market exit|Treat error as fatal|
|C10|Poll shows stop `filled`|Stop filled, not target|Book `ExecutionEvent(PROTECTIVE_STOP, FILLED)`, set `stop_state=filled, target_state=cancelled_by_stop_fill`, return|Also submit target exit|
|C11|Poll timeout / `filled|canceled` not reached within 5s (or `rejected` with no fill)|Cancellation unconfirmed|Mark manual intervention, raise `AmbiguousOrderState`|Assume canceled|
|C12|Exit market order not filled within 5s, or partially filled|Outcome unknown|If partial → cancel remainder then poll; else mark manual intervention `AmbiguousOrderState`|Leave dangling order|

---

## 8. Reconciliation — Crypto Exits & Stale Entries

|#|Broker observation|Proven fact|Allowed|Forbidden|
|---|---|---|---|---|
|R-C1|`managed_entries_for_account` has `exit_client_order_id` set (crypto)|Exit already submitted|Drive via `_reconcile_crypto_exit`: fetch by client id, map status → `ExecutionEvent`, handle filled/partial/canceled, re-protect if terminal|Ignore|
|R-C2|`exit_client_order_id` lookup raises|Ambiguous|Mark manual intervention, raise `ProtectionReconciliationError`|Assume terminal|
|R-C3|Exit `filled`|Exit completed|`set_exit_state(target_exit_filled|emergency_exit_filled)`|Leave pending|
|R-C4|Exit `partially_filled`|Partial|`set_exit_state(target_exit_partially_filled)` then try cancel remainder, wait with `crypto_stop_cancel_timeout`|Assume filled|
|R-C5|Exit `canceled|rejected|expired`|Terminal without full fill, shares remain|Call `_reprotect_after_terminal_exit` (verify `resolve_available_position_qty` → re-submit stop or flatten)|Leave unprotected|
|R-S1|Managed symbol absent from broker snapshot and `owned_qty>0` and no `exit_client_order_id`|Broker proves flat (position closed externally e.g. bracket filled while stream offline)|If equity and fill activities not yet reconciled → defer via `mark_manual_intervention` + await activity; else `mark_position_closed` (ledger sync) + log warning|Keep stale entry forever halting reconcile|
|R-S2|Same but crypto with `exit_client_order_id`|Exit pending|Defer to `_reconcile_crypto_exit`|Close ledger prematurely|
|R-P1|Fill activity `GET /account/activities/FILL` page has `commission|fee` and `transaction_time`|Fee + fill truth|Upsert via `apply_fee_activity` / `apply_execution_event` idempotent (stable activity id, ON CONFLICT), watermarked 24h look-back; buy fills → entry recovery if newer cumulative; sell fills → `MANUAL_EXIT` or fee-only if already booked|Double-count fee or invent fill|
|R-P2|Paginated fill activities cursor not advancing (duplicate token)|API pagination stalled|Log warning, break pagination, skip older activities (preserve safety)|Infinite loop|
|R-P3|Stale portfolio symbol (repo owned but broker flat) and `_fetch_broker_order` shows filled|Real stop filled at actual price|Recover via `portfolio_fill_recovered` at real qty/price, return True (handled)|Invent synthetic precise fill|
|R-P4|Same but broker shows working order|Exit still working|Return True (keep ownership)|Clear stale ownership|
|R-P5|No info at broker for stale symbol|Unknown|Return False → caller records `portfolio_flat_reconciled` adjustment (explicit anomaly)|Assume filled|

---

## 9. Peak & Risk Watermark

|#|Broker/DB observation|Proven fact|Allowed|Forbidden|
|---|---|---|---|---|
|K1|New account id `A` first `account()`|Account-scoped peak not yet bound|`_ensure_peak_reconciled(A, equity)` loads sidecar + `SELECT portfolio_high_water, risk_state WHERE account_id=A`, decides per Case A-F, binds `_peak_account_id=A`, rewrites sidecar atomically if legacy/mismatch|Use legacy sidecar peak from different account|
|K2|Sidecar `{"account_id": A, "peak": P1, "epoch": E1}` + DB `hw=P2,E2` same epoch|Both peaks for same account+epoch|`max(P1,P2)` wins, epoch `E1`|Average or min|
|K3|Same but epochs differ|Newer epoch wins (higher epoch)|Pick peak of higher epoch|Pick lower epoch|
|K4|SQLite read of `portfolio_high_water` or `risk_state` fails (non-"no such table")|Persistence unavailable|Raise `RiskStatePersistenceError`|Ignore and use 0|
|K5|All `risk_state` rows for account corrupt/unreadable|Durability failure|Raise `RiskStatePersistenceError(all unreadable)`|Treat as no history|

---

## 10. Idempotency & “Never Do” List

- **Never** call `close_position(symbol)` — closes full account qty and can consume manual inventory; use `MarketOrderRequest` with verified `resolve_available_position_qty`.
- **Never** synthesize a fill price (use broker `filled_avg_price` or recovered activity price).
- **Never** suppress drift with a manual/bulk BUY order, or a SELL of different market/symbol, or an undersized SELL (must cover shortfall).
- **Never** retry `submit_portfolio_order` with same `client_order_id` blindly — always `resolve_portfolio_submission` first.
- **Never** treat a network error as proof of cancel/fill — poll bounded, then `AmbiguousOrderState` + manual intervention.
- **Never** re-submit protective stop when fee-reserve band says it's healthy (churn).
- **Never** erase peak watermark by falling back to 0.0 on corrupt sidecar.

---

## 11. Verification

- `rg -n "CRYPTO_TAKER_FEE_RESERVE|CryptoPriceObservation" src/parayabuck/execution/alpaca.py` → lines 54,99
- `rg -n "AmbiguousOrderState|ProtectionReconciliationError|OwnershipDriftError|RiskStatePersistenceError" src/parayabuck/execution/alpaca.py` → 57,85,89,93
- `rg -n "def (account|reconcile|submit_portfolio|resolve_portfolio|process_crypto|snapshot_if_fresh)" src/parayabuck/execution/alpaca.py` → matches tables above
- `python -m py_compile src/parayabuck/execution/alpaca.py` passes (no behavior change)

*End truth matrix — every row must have a test or a log that proves the fact before the allowed action.*
