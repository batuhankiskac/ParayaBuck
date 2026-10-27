# Round 3 raw findings (performance) — unverified agent output

## Slice A — runtime.py 1–3016
- P-A1 medium runtime.py:2874,2911,2993 — has_order, order_side and _persist_risk_state run sync SQLite (shared store lock) on the trade-update asyncio loop; neighbours already use to_thread. Fix: wrap in asyncio.to_thread.
- P-A2 low runtime.py:2267-2271,2375-2377 + ledger.py:406-607 — every 30 s pass commits 3+ unchanged writes (risk_state upsert, high-water, DELETE safety incident) and re-runs a 4-table fee SUM. Fix: skip unchanged writes, cache fee total.
- P-A3 low runtime.py:733-835 + web/services/dashboard.py:508-526 — capacity_health makes uncached broker REST calls; the 10 s dashboard cache has no single-flight.
(Note: account loop polls every 15 s — config.py:400 — not 30 s.)

## Slice B — runtime.py 3017–end
- P-B1 medium data/aggregator.py:50-51,174-175 (runtime.py:3167-3172) — the daily bucket is re-sorted and re-aggregated over up to 390 components on every minute bar, under a threading.Lock inside the async _on_bar. Fix: running OHLCV; rebuild only on corrections.
- P-B2 low-medium portfolio/runtime_service.py:861-872 (runtime.py:5741-5746) — seed() rebuilds timestamp sets for all symbols on each per-symbol call during warmup (O(N²·T)) under two locks. Fix: seed per batch.
- P-B3 low runtime.py:4882-4904 — _refresh_readiness recomputes the fallback expected_latest per symbol inside _state_lock.
- P-B4 low ledger.py:406-457 — total_fees_for_account NOT EXISTS against portfolio_execution_events has no index on client_order_id (only UNIQUE(source, dedup_identity)). Fix: index or running total. (Same hot path as P-A2.)
- P-B5 low runtime.py:4577-4586,4815-4818 — failed signal-basis verification not negatively cached → blocking provider calls with sleep on every latch during an outage.
- P-B6 low runtime.py:5638 — worker restarts append to self._threads without pruning dead threads.

## Slice K — monitoring (note: agent wrongly said no sd_notify/WatchdogSec exists; utils/sd_notify.py and the unit's WatchdogSec do exist — ignore that premise line)
- P-K1 medium monitoring/health.py:462-525, metrics.py:240-336, forensics.py:74-157 — evaluate_health (dashboard /api/ops/status every 10 s per tab) opens ~6 ro connections and ~14 statements; reads ALL risk_state payload rows unused; lower(status) scans with no status index; SELECT * rebalance table filtered in Python. Fix: one connection, narrow queries, indexes.
- P-K2 low (growing) runtime_repository.py:187,242; ledger.py:167-183 — portfolio_orders, rebalance_executions, risk_state, runtime_sessions never pruned. Decide retention or document append-only.
- P-K3 medium-low web/services/status.py:19-81 + config.py:987-992 — /api/status (polled every 2 s per tab, ui.js:535) re-parses settings.yaml + .env and rebuilds the strategy each request. Fix: cache by path+mtime.
- P-K4 low-medium monitoring/observe.py:80-87 — "reconciliation_succeeded" journaled every 60 s with no dedup (~1,440 rows/day, near the 50k cap with 30-day retention → crowds out real events). Fix: dedup.
- P-K5 low journal.py:157-271 — writer opens a fresh connection + 4 PRAGMAs per batch. Fix: keep one connection.
- P-K6 latent journal.py:494-546 — emit_journal(path=non-global) leaks a journal + writer thread per call.
- P-K7 low web/routers/health.py:47-70 — async /readyz runs sync load_config and runtime.health() on the event loop.

## Slice I — web (frontend polling: /api/status 2 s; /ws/stream push ~2 s; overview 5 s; ops ×3 10 s; tax/performance 60 s; backtest status 3 s)
- P-I1 high services/status.py:105-111 + runtime_repository.py:1906-1950 — /api/status (2 s per tab) runs operations_snapshot: fills join ORDER BY occurred_at with no index, under the shared trading store lock. Fix: index portfolio_fills(occurred_at) / ro connection / change-counter cache.
- P-I2 high managers/engine.py:1056-1131 — fetch_account_snapshot holds _account_lock across broker REST (≤15 s) and doesn't memoize failures → during a broker outage every WS client/overview/performance caller serially retries and pins default-executor threads (5-6 on a small VM), starving other to_thread handlers. Fix: negative cache, single-flight outside the lock.
- P-I3 medium routers/websocket.py:76-84 — per-connection stream loop, not a broadcaster.
- P-I4 medium routers/health.py:47-67, services/settings.py:125-358 — sync YAML I/O, lock acquire, runtime.health() and reset_broker() directly in async handlers → can stall the event loop up to 15 s when the account lock is contended. Fix: to_thread.
- P-I5 medium services/status.py:51-99, managers/engine.py:257-283 — 2 s poll re-parses YAML and .env, calls runtime.health() twice, constructs the strategy. Fix: mtime cache. (Same as P-K3.)
- P-I6 low-medium routers/ops.py, services/ops.py — 3 ops endpoints every 10 s each re-parse config and open fresh SQLite connections. (Overlaps P-K1.)
- P-I7 low services/dashboard.py:752-801 — 1.5 s snapshot cache never hits at the 5 s poll; stale comment; no single-flight.
- P-I8 low static/js/websocket.js:52-57 — logs websocket reconnects every 2 s with no backoff.
- P-I9 low managers/websocket.py:62-82 — log handler formats every record before checking for subscribers.
- P-I10 low managers/backtest.py:174-210 — ProgressStream keeps full stdout/stderr in StringIO.
- P-I11 low services/performance.py:57-153 — new data client per miss, never closed; errors not cached.

## Slice H — backtest, data, strategy
- P-H1 high (backtests only) portfolio/equity_leveraged_trend.py:331,363-369,425 — R2 replay recomputes ~377 20-day vols and 126 medians of 252 on every decision (~6,500 decisions per 26-yr run; est. 10-20 ms each); consecutive decisions share 376/377 windows. Fix: lru_cache keyed on tuples (bit-identical); do NOT hand-roll stdev.
- P-H2 medium (offline reports) backtest/product.py:800-849 — cost_sensitivity (5 runs) and fill_timing_report (4 runs) rebuild universe/prepared window and the calendar check each run; optimize_grid already reuses them.
- P-H3 low data/aggregator.py:51,174-175 — same as P-B1 (re-sort per minute bar).
- P-H4 low/conditional portfolio_engine.py:1464-1495 — benchmark history exempt from trim, copied per session if visible.
- P-H5 low data/alpaca.py:238,386,396 — daily row budget counts calendar days not sessions → ~1.45× more provider requests.
- P-H6 low data/alpaca.py:347-373,541-544; cache.py:138 — per-symbol cache SELECT + Bar construction before deciding to fetch.
- P-H7 low data/cache.py:52-55 — no PRAGMA synchronous=NORMAL under WAL; no pruning.

## Slice C — runtime_repository, storage, ledger (store = one connection + RLock shared by all trading writes)
- P-C1 high ledger.py:406-457 — total_fees_for_account all-time NOT EXISTS against portfolio_execution_events with no client_order_id index, every 15 s pass + every fill, under the shared lock. (= P-A2/P-B4; three agents independently.) Fix: index (client_order_id, dedup_identity, fee_observed) or running total.
- P-C2 medium runtime_repository.py:1514-1557 — owned_quantities rescans all account orders + adjustments on every broker.account() (every pass). Fix: incremental per-symbol net qty or write-generation cache.
- P-C3 medium runtime_repository.py:1906-1995 — operations_snapshot (2 s /api/status) does 3 all-time scans under one lock hold; fills ORDER BY occurred_at unindexed; adjustments unbounded. (= P-I1.)
- P-C4 medium-low runtime.py:2874,2911 — same as P-A1 (sync lock on event loop).
- P-C5 low-medium runtime_repository.py:1381-1391,1607-1651 — each SELL fill replays full FIFO history; no index on portfolio_orders(symbol, market).
- P-C6 low-medium runtime_repository.py:1296-1302 — previous-event lookup filters on unindexed client_order_id (same index as P-C1 fixes it).
- P-C7 low (growing) — portfolio_* tables append-only, no retention (tax ledger reads adjustments, so prune carefully). (= P-K2.)
- P-C8 low runtime.py:2267-2377, ledger.py:459-607 — three commits per pass even when unchanged. (= P-A2.)
- P-C9 low runtime_repository.py:584-635 — watchdog reads/parses the same latest decision row 3× per 60 s.
- P-C10 low status.py:68-80 — config re-parse per 2 s poll. (= P-I5/P-K3.)

## Slice F — execution (baseline: one account() = 2 REST calls; 15 s poll ≈ 11.5k REST/day; reconcile pass ≈ 4 REST/60 s; 15 s timeouts OK)
- P-F1 medium runtime.py:743-745 + runtime_service.py:2857 — dashboard capacity probe makes 4 REST calls per 10 s, fetching the OPEN list twice; with overview + websocket ≈ 60 req/min vs Alpaca's ~200/min key limit. Fix: return the list/account already fetched, or a shared short-TTL broker read cache.
- P-F2 medium runtime.py:5000 vs alpaca_account.py:470 — total_fees_for_account computed TWICE per poll (snapshot already holds account.fees). Fix: use account.fees. (Plus index from P-C1.)
- P-F3 medium-low runtime_repository.py:1514-1552 — owned_quantities pulls every order row into Python; GROUP BY in SQL. (= P-C2.)
- P-F4 low-medium alpaca_reconciliation.py:503,590 — OPEN list fetched twice per reconcile even when nothing recovered.
- P-F5 medium (under throttling) alpaca_client.py:74 — alpaca-py retries 429/504 with fixed 3 s sleeps ×3 on every method including POST /orders. Fix: backoff+jitter, GET only.
- P-F6 low alpaca_reconciliation.py:189-329 — unknown-at-broker rows with non-cancelable status re-looked-up every pass forever.
- P-F7 low alpaca_client.py:207-215 — no negative cache for failed get_asset.
- P-F8/F9 low — 2 commits per poll when unchanged (= P-A2); no retention (= P-K2/P-C7).
- P-F10 low runtime_repository.py:1620-1635 — cost-basis replay filters orders by symbol with no index (= P-C5).
- P-F11 low runtime_service.py:2857-2862 — BUY admission bypasses the account snapshot cache.
- P-F12 low alpaca.py:398-418 — portfolio_open_orders re-queried per stale symbol.
